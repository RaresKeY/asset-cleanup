#!/usr/bin/env python3
"""Inspect selected CA stores with stdlib/OpenSSL; no project imports or downloads.

Invoke with python -I. --python imports installed certifi selectors, not project
code. This is trust-data inspection, not replacement certificate verification.
"""
import argparse
import hashlib
import importlib
import json
from pathlib import Path
import re
import socket
import ssl
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent
POLICY = json.loads((ROOT / 'incident-policy.json').read_text())['blocked_spki_sha256']
CERT_KEYS = {}


def inspect(path):
    path = Path(path)
    data = path.read_bytes()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cafile=str(path))
    if not context.cert_store_stats()['x509']:
        raise ValueError(f'{path}: empty store')
    for pem in re.findall(rb'-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----', data, re.S):
        fingerprint = hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem.decode())).hexdigest()
        if fingerprint not in CERT_KEYS:
            pub = subprocess.run(['openssl', 'x509', '-pubkey', '-noout'], input=pem,
                                 check=True, capture_output=True, timeout=10).stdout
            der = subprocess.run(['openssl', 'pkey', '-pubin', '-outform', 'DER'], input=pub,
                                 check=True, capture_output=True, timeout=10).stdout
            CERT_KEYS[fingerprint] = hashlib.sha256(der).hexdigest()
        key = CERT_KEYS[fingerprint]
        if key in POLICY:
            raise ValueError(f'{path}: distrusted CA key: {POLICY[key]}')
    return {'path': str(path), 'resolved': str(path.resolve()), 'sha256': hashlib.sha256(data).hexdigest()}


def chain_test(bundle, directory, chains, control):
    results = []
    with tempfile.TemporaryDirectory(prefix='ca-chain-check-') as temp, zipfile.ZipFile(chains) as archive:
        temp = Path(temp)
        members = [n for n in archive.namelist() if n.endswith('.pem')]
        if len(members) != 12:
            raise ValueError('Expected exactly twelve incident chains')
        for label, store, capath, expected in [('selected-file', bundle, None, 0),
                ('selected-directory', None, directory, 0), ('affected-control', control, None, 12)]:
            if store is None and capath is None:
                continue
            accepted = 0
            for member in members:
                certs = re.findall(rb'-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----', archive.read(member), re.S)
                if len(certs) < 2:
                    raise ValueError('Incomplete chain fixture')
                (temp / 'leaf.pem').write_bytes(certs[0] + b'\n')
                (temp / 'chain.pem').write_bytes(b'\n'.join(certs[1:]) + b'\n')
                args = ['openssl', 'verify', '-no-CAfile', '-no-CApath', '-no-CAstore',
                        '-purpose', 'sslserver', '-verify_hostname', Path(member).stem,
                        '-x509_strict', '-partial_chain', '-attime', '1792605600',
                        '-untrusted', str(temp / 'chain.pem')]
                if store: args += ['-CAfile', str(store)]
                if capath: args += ['-CApath', str(capath)]
                trial = subprocess.run(args + [str(temp / 'leaf.pem')], capture_output=True, timeout=15)
                accepted += trial.returncode == 0
            results.append({'store': label, 'accepted': accepted, 'expected': expected})
            if accepted != expected:
                raise ValueError(f'{label}: accepted {accepted}/12, expected {expected}')
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--system', action='store_true')
    parser.add_argument('--python', action='store_true')
    parser.add_argument('--bundle', type=Path)
    parser.add_argument('--directory', type=Path)
    parser.add_argument('--chains', type=Path)
    parser.add_argument('--control', type=Path)
    parser.add_argument('--live', action='store_true')
    args = parser.parse_args()
    paths = []; directory = args.directory; bundle = args.bundle
    if args.system:
        defaults = ssl.get_default_verify_paths()
        bundle = bundle or (Path(defaults.cafile) if defaults.cafile else None)
        directory = directory or (Path(defaults.capath) if defaults.capath else None)
    if bundle: paths.append(bundle)
    if directory:
        if not directory.is_dir(): raise ValueError('Missing CA directory')
        paths.extend(p for p in directory.iterdir() if re.fullmatch(r'[0-9a-fA-F]{8}\.[0-9]+', p.name) and p.is_file())
    if args.python:
        for name in ['certifi', 'pip._vendor.certifi']:
            try: module = importlib.import_module(name)
            except ModuleNotFoundError as e:
                if e.name in ['certifi', 'pip']: continue
                raise
            paths.append(Path(module.where()))
    if not paths: raise ValueError('No selected stores')
    stores = [inspect(p) for p in sorted(set(paths))]
    result = {'stores': stores, 'checked_files': len(stores)}
    if args.chains:
        if not args.control: raise ValueError('--chains requires --control for a usable-fixture control')
        result['chains'] = chain_test(bundle, directory, args.chains, args.control)
    if args.live:
        live = []
        for host, should_pass in [('pypi.org', True), ('dv-g3b-emudhra.we-have-a-problem.com', False),
                                  ('dv-g3b-aaa-emudhra.we-have-a-problem.com', False)]:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.load_verify_locations(cafile=str(bundle) if bundle else None,
                                          capath=str(directory) if directory else None)
            try:
                with socket.create_connection((host, 443), timeout=20) as raw:
                    with context.wrap_socket(raw, server_hostname=host): pass
                accepted = True
            except ssl.SSLCertVerificationError:
                accepted = False
            if accepted != should_pass: raise ValueError(f'{host}: unexpected TLS result')
            live.append({'host': host, 'accepted': accepted})
        result['live'] = live
    result['passed'] = True
    print(json.dumps(result))


if __name__ == '__main__':
    try: main()
    except (OSError, ValueError, ssl.SSLError, subprocess.SubprocessError) as error:
        print(f'CA trust check failed: {error}', file=sys.stderr)
        sys.exit(78)
