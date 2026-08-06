"""Bounded source classification and scene-preserving mesh loading."""

from __future__ import annotations

import base64
import binascii
import json
import re
import shlex
import struct
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, unquote_to_bytes, urlparse

import numpy as np

from asset_cleanup.errors import InputRejectedError


@dataclass(frozen=True, slots=True)
class LoadLimits:
    """Hard parser limits used before a recipe is trusted."""

    max_file_bytes: int = 2 * 1024 * 1024 * 1024
    max_vertices: int = 25_000_000
    max_faces: int = 50_000_000
    max_geometries: int = 10_000
    max_nodes: int = 50_000
    max_header_bytes: int = 1024 * 1024
    max_json_bytes: int = 32 * 1024 * 1024
    max_data_uri_bytes: int = 16 * 1024 * 1024
    max_referenced_files: int = 4_096
    max_referenced_bytes: int = 2 * 1024 * 1024 * 1024
    max_texture_pixels: int = 268_435_456


@dataclass(frozen=True, slots=True)
class SourceProbe:
    """Content-derived source classification; extensions are advisory only."""

    path: str
    source_kind: str
    format: str
    media_type: str
    supported: bool
    reason: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _read_prefix(path: Path, limit: int) -> bytes:
    with path.open("rb") as stream:
        return stream.read(limit)


def _probe_glb(path: Path, prefix: bytes, size: int) -> SourceProbe | None:
    if not prefix.startswith(b"glTF"):
        return None
    if len(prefix) < 12:
        return SourceProbe(str(path), "mesh", "glb", "model/gltf-binary", False, "short GLB header")
    version, declared = struct.unpack_from("<II", prefix, 4)
    supported = version == 2 and declared == size
    reason = (
        None if supported else f"GLB version/length mismatch (version={version}, length={declared})"
    )
    return SourceProbe(
        str(path),
        "mesh",
        "glb",
        "model/gltf-binary",
        supported,
        reason,
        {"version": version, "declared_bytes": declared, "actual_bytes": size},
    )


def _probe_ply(path: Path, prefix: bytes) -> SourceProbe | None:
    if not prefix.startswith(b"ply\n") and not prefix.startswith(b"ply\r\n"):
        return None
    marker = re.search(rb"(?:\r?\n)end_header(?:\r?\n)", prefix)
    if marker is None:
        return SourceProbe(
            str(path),
            "mesh",
            "ply",
            "application/octet-stream",
            False,
            "PLY header exceeds limit or is incomplete",
        )
    header = prefix[: marker.end()].decode("ascii", errors="replace")
    format_match = re.search(r"^format\s+(\S+)\s+", header, flags=re.MULTILINE)
    vertex_match = re.search(r"^element\s+vertex\s+(\d+)\s*$", header, flags=re.MULTILINE)
    face_match = re.search(r"^element\s+face\s+(\d+)\s*$", header, flags=re.MULTILINE)
    vertices = int(vertex_match.group(1)) if vertex_match else 0
    faces = int(face_match.group(1)) if face_match else 0
    source_kind = "mesh" if faces > 0 else "point-cloud"
    supported = faces > 0
    reason = (
        None
        if supported
        else "PLY has no polygon face element; point/Gaussian reconstruction requires an adapter"
    )
    return SourceProbe(
        str(path),
        source_kind,
        "ply",
        "application/octet-stream",
        supported,
        reason,
        {
            "encoding": format_match.group(1) if format_match else "unknown",
            "declared_vertices": vertices,
            "declared_faces": faces,
        },
    )


def _probe_gltf(path: Path, prefix: bytes, size: int, limits: LoadLimits) -> SourceProbe | None:
    stripped = prefix.lstrip()
    if not stripped.startswith(b"{"):
        return None
    if size > limits.max_json_bytes:
        return SourceProbe(
            str(path),
            "mesh",
            "gltf",
            "model/gltf+json",
            False,
            "glTF JSON exceeds configured limit",
        )
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    asset = document.get("asset") if isinstance(document, dict) else None
    if not isinstance(asset, dict) or not str(asset.get("version", "")).startswith("2"):
        return None
    return SourceProbe(
        str(path),
        "mesh",
        "gltf",
        "model/gltf+json",
        True,
        details={
            "version": asset.get("version"),
            "nodes": len(document.get("nodes", [])),
            "meshes": len(document.get("meshes", [])),
            "extensions_used": document.get("extensionsUsed", []),
            "extensions_required": document.get("extensionsRequired", []),
        },
    )


def _probe_obj(path: Path, prefix: bytes, limits: LoadLimits) -> SourceProbe | None:
    try:
        text = prefix.decode("utf-8")
    except UnicodeDecodeError:
        return None
    vertices = sum(1 for line in text.splitlines() if line.startswith("v "))
    faces = sum(1 for line in text.splitlines() if line.startswith("f "))
    if vertices == 0 and faces == 0:
        with path.open("rb") as stream:
            for raw_line in stream:
                if len(raw_line) > limits.max_header_bytes:
                    return None
                stripped = raw_line.lstrip()
                vertices += stripped.startswith(b"v ")
                faces += stripped.startswith(b"f ")
                if vertices and faces:
                    break
        if vertices == 0 and faces == 0:
            return None
    return SourceProbe(
        str(path),
        "mesh",
        "obj",
        "model/obj",
        faces > 0,
        None if faces > 0 else "OBJ prefix contains no faces",
        {"prefix_vertices": vertices, "prefix_faces": faces},
    )


def _probe_stl(path: Path, prefix: bytes, size: int) -> SourceProbe | None:
    if len(prefix) >= 84:
        triangles = struct.unpack_from("<I", prefix, 80)[0]
        if 84 + triangles * 50 == size:
            return SourceProbe(
                str(path),
                "mesh",
                "stl",
                "model/stl",
                True,
                details={"triangles": triangles, "encoding": "binary"},
            )
    stripped = prefix.lstrip().lower()
    if stripped.startswith(b"solid") and b"facet normal" in stripped:
        return SourceProbe(
            str(path), "mesh", "stl", "model/stl", True, details={"encoding": "ascii"}
        )
    return None


def probe_source(path: Path, limits: LoadLimits | None = None) -> SourceProbe:
    """Classify a supported source from bounded content reads."""

    limits = limits or LoadLimits()
    path = path.expanduser().resolve()
    if not path.is_file():
        raise InputRejectedError(f"source is not a regular file: {path}")
    size = path.stat().st_size
    if size <= 0:
        raise InputRejectedError("source is empty")
    if size > limits.max_file_bytes:
        raise InputRejectedError(
            f"source exceeds max_file_bytes ({size} > {limits.max_file_bytes})"
        )
    prefix = _read_prefix(path, max(limits.max_header_bytes, 84))

    detectors: tuple[Callable[[], SourceProbe | None], ...] = (
        lambda: _probe_glb(path, prefix, size),
        lambda: _probe_ply(path, prefix),
        lambda: _probe_gltf(path, prefix, size, limits),
        lambda: _probe_obj(path, prefix, limits),
        lambda: _probe_stl(path, prefix, size),
    )
    for detector in detectors:
        detected = detector()
        if detected is not None:
            return detected

    if prefix.startswith(b"PK\x03\x04"):
        return SourceProbe(
            str(path),
            "bundle",
            "zip",
            "application/zip",
            False,
            "bundle ingestion is available through the web intake layer, not the core mesh loader",
        )
    if path.suffix.lower() == ".bin" or path.name.lower().endswith("post.bin"):
        return SourceProbe(
            str(path),
            "trellis-post",
            "trellis-post-bin",
            "application/octet-stream",
            False,
            "retained TRELLIS captures require a pinned replay provider and are "
            "never generically deserialized",
        )
    return SourceProbe(
        str(path),
        "unknown",
        "unknown",
        "application/octet-stream",
        False,
        "unrecognized or unsupported source content",
    )


def _gltf_document(path: Path, probe: SourceProbe, limits: LoadLimits) -> dict[str, Any] | None:
    if probe.format == "gltf":
        document = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise InputRejectedError("glTF JSON document must be an object")
        return document
    if probe.format != "glb":
        return None
    with path.open("rb") as stream:
        header = stream.read(12)
        if len(header) != 12:
            raise InputRejectedError("short GLB header")
        chunk_header = stream.read(8)
        if len(chunk_header) != 8:
            raise InputRejectedError("GLB is missing its JSON chunk")
        length, chunk_type = struct.unpack("<II", chunk_header)
        if chunk_type != 0x4E4F534A or length > limits.max_json_bytes:
            raise InputRejectedError("invalid or oversized GLB JSON chunk")
        payload = stream.read(length)
    try:
        document = json.loads(payload.rstrip(b"\x00 \t\r\n").decode("utf-8"))
        if not isinstance(document, dict):
            raise InputRejectedError("GLB JSON document must be an object")
        return document
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InputRejectedError(f"invalid GLB JSON: {exc}") from exc


def _relative_reference(
    uri: str,
    *,
    reference_root: Path,
    bundle_root: Path,
    limits: LoadLimits,
) -> tuple[str, Path] | None:
    """Resolve one dependency without permitting an external or escaping path."""

    parsed = urlparse(uri)
    if parsed.scheme == "data":
        _decode_data_uri(uri, limits)
        return None
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        raise InputRejectedError(f"external or decorated URI is not allowed: {uri[:120]}")
    decoded = unquote(parsed.path)
    if decoded.startswith(("/", "\\")) or "\x00" in decoded:
        raise InputRejectedError(f"external or absolute URI is not allowed: {uri[:120]}")
    normalized = PurePosixPath(decoded.replace("\\", "/"))
    if ".." in normalized.parts:
        raise InputRejectedError(f"URI escapes its bundle: {uri[:120]}")
    resolved_bundle = bundle_root.resolve()
    resolved = (reference_root / Path(*normalized.parts)).resolve()
    if resolved_bundle not in resolved.parents and resolved != resolved_bundle:
        raise InputRejectedError(f"URI escapes its bundle: {uri[:120]}")
    if not resolved.is_file():
        raise InputRejectedError(f"referenced bundle member is missing: {uri[:120]}")
    if resolved.stat().st_size > limits.max_file_bytes:
        raise InputRejectedError(f"referenced bundle member exceeds configured limit: {uri[:120]}")
    relative = resolved.relative_to(resolved_bundle).as_posix()
    return relative, resolved


def _decode_data_uri(uri: str, limits: LoadLimits) -> tuple[str, bytes]:
    """Decode one bounded data URI and return its declared media type and bytes."""

    try:
        header, payload = uri.split(",", 1)
    except ValueError as exc:
        raise InputRejectedError("invalid data URI") from exc
    metadata = header[5:] if header.lower().startswith("data:") else ""
    media_type = metadata.split(";", 1)[0].lower()
    try:
        if ";base64" in metadata.lower():
            # Reject before decoding when the encoded representation cannot
            # possibly fit. The exact decoded length is checked afterwards.
            if (len(payload) * 3 + 3) // 4 > limits.max_data_uri_bytes + 2:
                raise InputRejectedError("data URI exceeds configured limit")
            decoded = base64.b64decode(payload, validate=True)
        else:
            decoded = unquote_to_bytes(payload)
    except (ValueError, binascii.Error) as exc:
        raise InputRejectedError("invalid data URI payload") from exc
    if len(decoded) > limits.max_data_uri_bytes:
        raise InputRejectedError("data URI exceeds configured limit")
    return media_type, decoded


def _image_dimensions(prefix: bytes) -> tuple[int, int] | None:
    """Read common texture dimensions from a bounded encoded prefix."""

    if len(prefix) >= 24 and prefix.startswith(b"\x89PNG\r\n\x1a\n"):
        return struct.unpack(">II", prefix[16:24])
    if len(prefix) >= 10 and prefix[:6] in {b"GIF87a", b"GIF89a"}:
        return struct.unpack("<HH", prefix[6:10])
    if len(prefix) >= 30 and prefix.startswith(b"RIFF") and prefix[8:12] == b"WEBP":
        kind = prefix[12:16]
        if kind == b"VP8X" and len(prefix) >= 30:
            width = 1 + int.from_bytes(prefix[24:27], "little")
            height = 1 + int.from_bytes(prefix[27:30], "little")
            return width, height
        if kind == b"VP8L" and len(prefix) >= 25 and prefix[20] == 0x2F:
            bits = int.from_bytes(prefix[21:25], "little")
            return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if len(prefix) >= 32 and prefix.startswith(b"\xabKTX 20\xbb\r\n\x1a\n"):
        return struct.unpack_from("<II", prefix, 20)
    if len(prefix) >= 20 and prefix.startswith(b"DDS "):
        height, width = struct.unpack_from("<II", prefix, 12)
        return width, height
    if prefix.startswith(b"\xff\xd8"):
        offset = 2
        start_of_frame = {
            0xC0,
            0xC1,
            0xC2,
            0xC3,
            0xC5,
            0xC6,
            0xC7,
            0xC9,
            0xCA,
            0xCB,
            0xCD,
            0xCE,
            0xCF,
        }
        while offset + 4 <= len(prefix):
            if prefix[offset] != 0xFF:
                offset += 1
                continue
            marker = prefix[offset + 1]
            offset += 2
            if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
                continue
            if offset + 2 > len(prefix):
                break
            length = int.from_bytes(prefix[offset : offset + 2], "big")
            if length < 2 or offset + length > len(prefix):
                break
            if marker in start_of_frame and length >= 7:
                height = int.from_bytes(prefix[offset + 3 : offset + 5], "big")
                width = int.from_bytes(prefix[offset + 5 : offset + 7], "big")
                return width, height
            offset += length
    return None


def _validate_texture_prefix(prefix: bytes, limits: LoadLimits, label: str) -> None:
    dimensions = _image_dimensions(prefix)
    if dimensions is None:
        raise InputRejectedError(f"cannot safely determine texture dimensions: {label}")
    width, height = dimensions
    if width <= 0 or height <= 0:
        raise InputRejectedError(f"texture has invalid dimensions: {label}")
    if width * height > limits.max_texture_pixels:
        raise InputRejectedError(f"texture exceeds max_texture_pixels ({width}x{height}): {label}")


def _validate_texture_path(path: Path, limits: LoadLimits, label: str) -> None:
    _validate_texture_prefix(_read_prefix(path, limits.max_header_bytes), limits, label)


def _glb_binary_chunk(path: Path) -> tuple[int, int] | None:
    """Return the byte offset and length of the first GLB BIN chunk."""

    file_size = path.stat().st_size
    with path.open("rb") as stream:
        stream.seek(12)
        while stream.tell() + 8 <= file_size:
            chunk_length, chunk_type = struct.unpack("<II", stream.read(8))
            data_offset = stream.tell()
            if chunk_length < 0 or data_offset + chunk_length > file_size:
                raise InputRejectedError("GLB contains an invalid chunk length")
            if chunk_type == 0x004E4942:
                return data_offset, chunk_length
            stream.seek(chunk_length, 1)
    return None


def _buffer_view_prefix(
    path: Path,
    probe: SourceProbe,
    document: dict[str, Any],
    view_index: int,
    limits: LoadLimits,
) -> bytes:
    views = document.get("bufferViews", [])
    buffers = document.get("buffers", [])
    if not isinstance(views, list) or not 0 <= view_index < len(views):
        raise InputRejectedError("glTF image references an invalid bufferView")
    view = views[view_index]
    if not isinstance(view, dict):
        raise InputRejectedError("glTF bufferView must be an object")
    buffer_index = view.get("buffer", 0)
    byte_offset = view.get("byteOffset", 0)
    byte_length = view.get("byteLength")
    if (
        isinstance(buffer_index, bool)
        or not isinstance(buffer_index, int)
        or isinstance(byte_offset, bool)
        or not isinstance(byte_offset, int)
        or isinstance(byte_length, bool)
        or not isinstance(byte_length, int)
        or byte_offset < 0
        or byte_length <= 0
    ):
        raise InputRejectedError("glTF image bufferView has invalid bounds")
    read_length = min(byte_length, limits.max_header_bytes)
    if probe.format == "glb" and buffer_index == 0:
        chunk = _glb_binary_chunk(path)
        if chunk is None:
            raise InputRejectedError("GLB image references a missing BIN chunk")
        chunk_offset, chunk_length = chunk
        if byte_offset + byte_length > chunk_length:
            raise InputRejectedError("GLB image bufferView exceeds its BIN chunk")
        with path.open("rb") as stream:
            stream.seek(chunk_offset + byte_offset)
            return stream.read(read_length)
    if not isinstance(buffers, list) or not 0 <= buffer_index < len(buffers):
        raise InputRejectedError("glTF image references an invalid buffer")
    buffer = buffers[buffer_index]
    if not isinstance(buffer, dict) or not isinstance(buffer.get("uri"), str):
        raise InputRejectedError("glTF image buffer has no inspectable URI")
    uri = str(buffer["uri"])
    parsed = urlparse(uri)
    if parsed.scheme == "data":
        _, payload = _decode_data_uri(uri, limits)
        if byte_offset + byte_length > len(payload):
            raise InputRejectedError("glTF image bufferView exceeds its data URI")
        return payload[byte_offset : byte_offset + read_length]
    member = _relative_reference(
        uri,
        reference_root=path.parent,
        bundle_root=path.parent,
        limits=limits,
    )
    if member is None:
        raise InputRejectedError("glTF image buffer URI is not inspectable")
    _, buffer_path = member
    if byte_offset + byte_length > buffer_path.stat().st_size:
        raise InputRejectedError("glTF image bufferView exceeds its external buffer")
    with buffer_path.open("rb") as stream:
        stream.seek(byte_offset)
        return stream.read(read_length)


def _validate_gltf_textures(
    path: Path,
    probe: SourceProbe,
    document: dict[str, Any],
    limits: LoadLimits,
) -> None:
    images = document.get("images", [])
    if not isinstance(images, list):
        raise InputRejectedError("glTF images must be an array")
    for index, item in enumerate(images):
        if not isinstance(item, dict):
            raise InputRejectedError("glTF image must be an object")
        label = f"image[{index}]"
        uri = item.get("uri")
        if isinstance(uri, str):
            if urlparse(uri).scheme == "data":
                _, payload = _decode_data_uri(uri, limits)
                _validate_texture_prefix(payload[: limits.max_header_bytes], limits, label)
            else:
                member = _relative_reference(
                    uri,
                    reference_root=path.parent,
                    bundle_root=path.parent,
                    limits=limits,
                )
                if member is None:
                    raise InputRejectedError(f"texture is not inspectable: {label}")
                _validate_texture_path(member[1], limits, member[0])
        elif isinstance(item.get("bufferView"), int) and not isinstance(
            item.get("bufferView"), bool
        ):
            prefix = _buffer_view_prefix(
                path,
                probe,
                document,
                int(item["bufferView"]),
                limits,
            )
            _validate_texture_prefix(prefix, limits, label)
        else:
            raise InputRejectedError(f"glTF image has no URI or bufferView: {label}")


def _validate_relative_uri(uri: str, root: Path, limits: LoadLimits) -> None:
    _relative_reference(
        uri,
        reference_root=root,
        bundle_root=root,
        limits=limits,
    )


def _obj_library_names(path: Path, limits: LoadLimits) -> list[str]:
    libraries: list[str] = []
    with path.open("rb") as stream:
        for raw_line in stream:
            if len(raw_line) > limits.max_header_bytes:
                raise InputRejectedError("OBJ line exceeds the configured parser limit")
            line = raw_line.decode("utf-8", errors="replace")
            stripped = line.strip()
            if not stripped.lower().startswith("mtllib "):
                continue
            try:
                libraries.extend(shlex.split(stripped, comments=True)[1:])
            except ValueError as exc:
                raise InputRejectedError(f"invalid OBJ mtllib declaration: {exc}") from exc
    return libraries


def _mtl_texture_names(path: Path, limits: LoadLimits) -> list[str]:
    names: list[str] = []
    directives = {"bump", "disp", "decal", "norm", "refl"}
    with path.open("rb") as stream:
        for raw_line in stream:
            if len(raw_line) > limits.max_header_bytes:
                raise InputRejectedError("MTL line exceeds the configured parser limit")
            line = raw_line.decode("utf-8", errors="replace")
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            try:
                fields = shlex.split(stripped, comments=True)
            except ValueError as exc:
                raise InputRejectedError(f"invalid MTL texture declaration: {exc}") from exc
            if len(fields) < 2:
                continue
            directive = fields[0].lower()
            if not (directive.startswith("map_") or directive in directives):
                continue
            # MTL map options precede the path. The final token is the only safe,
            # interoperable interpretation supported by this bounded intake layer.
            names.append(fields[-1])
    return names


def referenced_files(
    path: Path,
    probe: SourceProbe | None = None,
    limits: LoadLimits | None = None,
) -> list[tuple[str, Path]]:
    """Return confined external source members as bundle-relative paths."""

    limits = limits or LoadLimits()
    path = path.expanduser().resolve()
    probe = probe or probe_source(path, limits)
    members: dict[str, Path] = {}
    document = _gltf_document(path, probe, limits)
    if document is not None:
        _validate_gltf_textures(path, probe, document, limits)
        for section in ("buffers", "images"):
            items = document.get(section, [])
            if not isinstance(items, list):
                raise InputRejectedError(f"glTF {section} must be an array")
            for item in items:
                if isinstance(item, dict) and isinstance(item.get("uri"), str):
                    member = _relative_reference(
                        item["uri"],
                        reference_root=path.parent,
                        bundle_root=path.parent,
                        limits=limits,
                    )
                    if member is not None:
                        members[member[0]] = member[1]
    elif probe.format == "obj":
        for library_name in _obj_library_names(path, limits):
            library = _relative_reference(
                library_name,
                reference_root=path.parent,
                bundle_root=path.parent,
                limits=limits,
            )
            if library is None:
                continue
            members[library[0]] = library[1]
            for texture_name in _mtl_texture_names(library[1], limits):
                texture = _relative_reference(
                    texture_name,
                    reference_root=library[1].parent,
                    bundle_root=path.parent,
                    limits=limits,
                )
                if texture is not None:
                    _validate_texture_path(texture[1], limits, texture[0])
                    members[texture[0]] = texture[1]
    if len(members) > limits.max_referenced_files:
        raise InputRejectedError("source exceeds the referenced-file count limit")
    referenced_bytes = sum(member.stat().st_size for member in members.values())
    if referenced_bytes > limits.max_referenced_bytes:
        raise InputRejectedError("source exceeds the referenced-file byte limit")
    return sorted(members.items())


def validate_external_references(path: Path, probe: SourceProbe, limits: LoadLimits) -> None:
    """Reject network, absolute, traversal, missing, or oversized dependencies."""

    referenced_files(path, probe, limits)


def _preflight_gltf(document: dict[str, Any], limits: LoadLimits) -> None:
    nodes = document.get("nodes", [])
    meshes = document.get("meshes", [])
    accessors = document.get("accessors", [])
    if not all(isinstance(section, list) for section in (nodes, meshes, accessors)):
        raise InputRejectedError("glTF nodes, meshes, and accessors must be arrays")
    if len(nodes) > limits.max_nodes or len(meshes) > limits.max_geometries:
        raise InputRejectedError("glTF scene inventory exceeds configured limits")

    def accessor_count(index: Any) -> int:
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(accessors):
            raise InputRejectedError("glTF primitive references an invalid accessor")
        accessor = accessors[index]
        if not isinstance(accessor, dict):
            raise InputRejectedError("glTF accessor must be an object")
        count = accessor.get("count")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise InputRejectedError("glTF accessor has an invalid count")
        return count

    position_count = 0
    triangle_count = 0
    for mesh in meshes:
        if not isinstance(mesh, dict):
            raise InputRejectedError("glTF mesh must be an object")
        primitives = mesh.get("primitives", [])
        if not isinstance(primitives, list):
            raise InputRejectedError("glTF mesh primitives must be an array")
        for primitive in primitives:
            if not isinstance(primitive, dict):
                raise InputRejectedError("glTF primitive must be an object")
            attributes = primitive.get("attributes", {})
            if not isinstance(attributes, dict) or "POSITION" not in attributes:
                raise InputRejectedError("glTF primitive is missing a POSITION accessor")
            primitive_vertices = accessor_count(attributes["POSITION"])
            position_count += primitive_vertices
            indices_index = primitive.get("indices")
            element_count = (
                accessor_count(indices_index) if indices_index is not None else primitive_vertices
            )
            mode = primitive.get("mode", 4)
            if isinstance(mode, bool) or not isinstance(mode, int) or not 0 <= mode <= 6:
                raise InputRejectedError("glTF primitive has an invalid mode")
            if mode == 4:
                triangle_count += element_count // 3
            elif mode in {5, 6}:
                triangle_count += max(0, element_count - 2)
    if position_count > limits.max_vertices or triangle_count > limits.max_faces:
        raise InputRejectedError("glTF declared geometry exceeds configured limits")


def _preflight_source(path: Path, probe: SourceProbe, limits: LoadLimits) -> None:
    if probe.format in {"glb", "gltf"}:
        document = _gltf_document(path, probe, limits)
        if document is None:
            raise InputRejectedError("glTF source is missing its JSON document")
        _preflight_gltf(document, limits)
    elif probe.format == "ply":
        vertices = int(probe.details.get("declared_vertices", 0))
        faces = int(probe.details.get("declared_faces", 0))
        if vertices > limits.max_vertices or faces > limits.max_faces:
            raise InputRejectedError("PLY declared geometry exceeds configured limits")
    elif probe.format == "stl":
        triangles = int(probe.details.get("triangles", 0))
        if triangles > limits.max_faces:
            raise InputRejectedError("STL declared geometry exceeds configured limits")
    elif probe.format == "obj":
        vertices = 0
        faces = 0
        with path.open("rb") as stream:
            for raw_line in stream:
                if len(raw_line) > limits.max_header_bytes:
                    raise InputRejectedError("OBJ line exceeds the configured parser limit")
                stripped = raw_line.lstrip()
                vertices += stripped.startswith(b"v ")
                if stripped.startswith(b"f "):
                    faces += max(1, len(stripped.split()) - 3)
                if vertices > limits.max_vertices or faces > limits.max_faces:
                    raise InputRejectedError("OBJ geometry exceeds configured limits")


def load_scene(path: Path, limits: LoadLimits | None = None):  # type: ignore[no-untyped-def]
    """Load a supported source as a scene without flattening or repair processing."""

    import trimesh

    limits = limits or LoadLimits()
    path = path.expanduser().resolve()
    probe = probe_source(path, limits)
    if not probe.supported:
        raise InputRejectedError(probe.reason or f"unsupported source format: {probe.format}")
    validate_external_references(path, probe, limits)
    _preflight_source(path, probe, limits)
    try:
        scene = trimesh.load_scene(path, process=False)
    except BaseException as exc:
        raise InputRejectedError(f"mesh parser rejected {path.name}: {exc}") from exc
    if not scene.geometry:
        raise InputRejectedError("scene contains no triangle geometry")
    if len(scene.geometry) > limits.max_geometries:
        raise InputRejectedError("scene exceeds max_geometries")
    if len(scene.graph.nodes) > limits.max_nodes:
        raise InputRejectedError("scene exceeds max_nodes")
    vertices = 0
    faces = 0
    for name, geometry in scene.geometry.items():
        if not isinstance(geometry, trimesh.Trimesh):
            raise InputRejectedError(f"geometry {name!r} is not a triangle mesh")
        vertices += len(geometry.vertices)
        faces += len(geometry.faces)
        if not np.isfinite(np.asarray(geometry.vertices, dtype=float)).all():
            raise InputRejectedError(f"geometry {name!r} contains non-finite positions")
        if len(geometry.vertices) == 0 or len(geometry.faces) == 0:
            raise InputRejectedError(f"geometry {name!r} has no triangle surface")
        face_areas = np.asarray(geometry.area_faces, dtype=float)
        if not np.isfinite(face_areas).all() or not np.any(face_areas > np.finfo(float).eps):
            raise InputRejectedError(f"geometry {name!r} has no non-degenerate triangle surface")
    if vertices > limits.max_vertices or faces > limits.max_faces:
        raise InputRejectedError(
            f"scene exceeds geometry limits (vertices={vertices}, faces={faces})"
        )
    return scene


def iter_meshes(scene: Any) -> Iterator[tuple[str, Any]]:
    """Yield local-space mesh definitions without concatenating scene nodes."""

    import trimesh

    for name in sorted(scene.geometry):
        geometry = scene.geometry[name]
        if isinstance(geometry, trimesh.Trimesh):
            yield name, geometry


def merged_world_mesh(scene: Any):  # type: ignore[no-untyped-def]
    """Build a derived world-space mesh for whole-asset metrics or collision."""

    import trimesh

    transformed = []
    nodes_geometry = sorted(scene.graph.nodes_geometry)
    for node_name in nodes_geometry:
        transform, geometry_name = scene.graph.get(node_name)
        mesh = scene.geometry[geometry_name].copy()
        mesh.apply_transform(transform)
        transformed.append(mesh)
    if not transformed:
        raise InputRejectedError("scene contains no instanced triangle mesh")
    return trimesh.util.concatenate(transformed)
