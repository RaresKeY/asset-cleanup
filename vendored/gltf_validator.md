# Khronos glTF Validator provider

## Resolved component

The Linux OCI image bundles the official Khronos glTF Validator native CLI as a
fixed structural-conformance provider. It is not installed through npm and no
Dart or Node runtime is present in the final image.

| Property | Resolved value |
|---|---|
| Upstream | `KhronosGroup/glTF-Validator` |
| Release | `2.0.0-dev.3.10` (prerelease) |
| Asset | `gltf_validator-2.0.0-dev.3.10-linux64.tar.xz` |
| Platform | Linux amd64 |
| SHA-256 | `168eba887964125abe17ae97899b38d0b3cfd73c266c78424c194929ddcbc522` |
| Runtime path | `/usr/local/bin/gltf_validator` |
| License | Apache-2.0; upstream dependency notices are in `NOTICES` |

The build downloads only from the versioned upstream GitHub release URL,
verifies the complete archive before reading its members, and retains the
executable, `LICENSE`, and `NOTICES`. The final image stores the legal files
under `/usr/share/licenses/gltf-validator/` and
`/usr/share/doc/gltf-validator/`. Third-party license text is not copied into
`vendored/`.

## Adapter boundary

The application discovers a fixed executable identity; recipes cannot select a
path, command, configuration file, or arguments. Validation records a sanitized
fixed-argv contract without private paths and uses a mode-0600 private YAML
configuration capped at 1,000 issues, fixed argument-array invocation, a
120-second timeout, process-group termination, a 16 MiB JSON-report ceiling, and
a 64 KiB stderr tail. The adapter supplies a fixed default search path and
locale/time-zone values, retaining only required Windows loader roots on that
platform, and fails closed on timeout, output overflow, incomplete pipe drain,
malformed evidence, invalid counts, nonzero exit, glTF errors, or a report
version other than the pin. Evidence records provider identity, discovered and
reported versions, limits, elapsed time, return code, bounded stderr, issue
counts, and the parsed report. Timestamp and absolute-path fields are suppressed
for reproducibility.

The validator is conformance evidence, not a sanitizer. Core intake, reference,
resource, and output policy remains authoritative before and after the external
process. Unsupported extensions may receive only partial validation. Upstream
reports incomplete Draco payload validation and extension-coverage gaps; a
zero-error report therefore proves only the checks implemented by this pinned
provider. The reported greater-than-4-GiB length defect is outside the service's
current 1-GiB input ceiling.

## Update procedure

1. Recheck the official release, tag/commit, license, `NOTICES`, security page,
   open issues, and recent user reports.
2. Download the exact platform archive independently and calculate SHA-256.
3. Update the Dockerfile checksum, this record, root notices, version assertions,
   and adapter fixtures in one change.
4. Build without cache; run the synthetic glTF smoke, malformed-input tests,
   timeout/output-bound tests, and full container smoke.
5. Record any platform or behavior change in `specs/` before merge.

## Gaps

- Upstream does not publish a Linux arm64 native archive for this pin. The image
  build fails explicitly on non-amd64 platforms; source-building and validating
  an arm64 artifact is future work.
- The pinned asset is a prerelease and has no project-owned reproducible-build,
  Sigstore, or detached-signature verification. SHA-256 establishes exact bytes,
  not upstream authorship; the versioned GitHub release is the provenance root.
- Upstream advisories, issue regressions, and user reports are reviewed manually;
  automated monitoring and a scheduled refresh policy are not implemented.
- Extension coverage is incomplete, including compressed payloads; upgrade
  acceptance needs golden fixtures for formats the application advertises.
- Native parser fuzzing and a cross-version golden corpus are not yet part of the
  project verification matrix.
- Windows termination is best-effort and does not yet use a Job Object to prove
  descendant-tree cleanup; production bundling is currently Linux amd64.
