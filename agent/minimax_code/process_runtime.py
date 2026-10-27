"""Pinned Podman inspect schema, rootless map composition and log bounds."""
from __future__ import annotations

import re
from decimal import ROUND_CEILING, Decimal

from .process_types import ProcessBoundaryError, canonical_hash

SCHEMA = "podman-4.9.3-v1"
LOG_BOUND_VERSION = "human-size-4sig-si-upper-v1"
MAX_LOG_LIMIT = 1024**3
_ID_END = 2**32 - 1  # The unmapped uid_t sentinel is never a usable ID.
IdMap = list[list[int]]


def normalize_map(rows: object) -> IdMap:
    if type(rows) not in (list, tuple) or not 0 < len(rows) <= 340:
        raise ProcessBoundaryError("Missing or oversized user namespace mapping")
    normalized = []
    for row in rows:
        if type(row) not in (list, tuple) or len(row) != 3:
            raise ProcessBoundaryError("Malformed user namespace interval")
        start, host, size = row
        if (any(type(item) is not int for item in row) or start < 0 or host < 0
                or size <= 0 or start + size > _ID_END or host + size > _ID_END):
            raise ProcessBoundaryError("User namespace interval overflows valid IDs")
        normalized.append((start, host, size))
    for coordinate in (0, 1):
        ordered = sorted(normalized, key=lambda item: item[coordinate])
        if any(left[coordinate] + left[2] > right[coordinate]
               for left, right in zip(ordered, ordered[1:], strict=False)):
            raise ProcessBoundaryError("User namespace intervals overlap")
    result = []
    for row in sorted(normalized):
        if result and all(result[-1][index] + result[-1][2] == row[index] for index in (0, 1)):
            previous = result.pop()
            result.append((previous[0], previous[1], previous[2] + row[2]))
        else:
            result.append(row)
    return [list(row) for row in result]


def info_map(rows: object) -> IdMap:
    if type(rows) is not list or any(type(row) is not dict
            or set(row) != {"container_id", "host_id", "size"} for row in rows):
        raise ProcessBoundaryError("Unsupported Podman info ID mapping schema")
    return normalize_map([[row["container_id"], row["host_id"], row["size"]] for row in rows])


def inspect_map(rows: object) -> IdMap:
    if type(rows) is not list or any(type(row) is not str
            or len(row) > 32 or re.fullmatch(r"[0-9]+:[0-9]+:[0-9]+", row) is None for row in rows):
        raise ProcessBoundaryError("Unsupported Podman inspect ID mapping schema")
    return normalize_map([[int(item) for item in row.split(":")] for row in rows])


def proc_map(value: object) -> IdMap:
    if type(value) is not str or len(value) > 32768:
        raise ProcessBoundaryError("Unsupported kernel ID mapping evidence")
    rows = value.splitlines()
    if any(re.fullmatch(r"\s*[0-9]{1,10}\s+[0-9]{1,10}\s+[0-9]{1,10}\s*", row) is None for row in rows):
        raise ProcessBoundaryError("Malformed kernel ID mapping evidence")
    return normalize_map([[int(item) for item in row.split()] for row in rows])


def compose_maps(inner: object, outer: object) -> IdMap:
    """Map container -> rootless namespace -> actual host, with full coverage."""
    inner, outer = normalize_map(inner), normalize_map(outer)
    composed = []
    for container, intermediate, size in inner:
        cursor, end = intermediate, intermediate + size
        for source, host, length in outer:
            if source + length <= cursor:
                continue
            if source > cursor:
                break
            used = min(end, source + length) - cursor
            composed.append([container + cursor - intermediate, host + cursor - source, used])
            cursor += used
            if cursor == end:
                break
        if cursor != end:
            raise ProcessBoundaryError("Rootless outer mapping does not cover container IDs")
    return normalize_map(composed)


def container_mapping(host: dict, runtime: dict) -> dict:
    mappings = host.get("IDMappings")
    if type(mappings) is not dict:
        raise ProcessBoundaryError("Container user namespace mapping is unconfirmed")
    proof = {}
    for label, key, actual in (("uid", "UidMap", "host_uid"), ("gid", "GidMap", "host_gid")):
        inner = inspect_map(mappings.get(key))
        composed = compose_maps(inner, runtime[label + "_map"])
        desired = runtime[actual]
        selected = map_id(composed, desired)
        if selected != desired or map_id(composed, 0) == 0 or any(host_id == 0 for _, host_id, _ in composed):
            raise ProcessBoundaryError("Container mapping does not preserve the rootless owner")
        proof[label + "_map"] = inner
        proof[label + "_host_map"] = composed
    proof["fingerprint"] = canonical_hash(proof)
    return proof


def map_id(rows: IdMap, identifier: int) -> int:
    for source, destination, size in rows:
        if source <= identifier < source + size:
            return destination + identifier - source
    raise ProcessBoundaryError("Required user namespace ID is not mapped")


def runtime_evidence(data: object, *, uid: int, gid: int) -> dict:
    if type(data) is not dict:
        raise ProcessBoundaryError("Unsupported Podman info schema")
    host, version = data.get("host"), data.get("version")
    if (type(host) is not dict or type(version) is not dict or version.get("Version") != "4.9.3"
            or host.get("arch") != "amd64" or host.get("cgroupVersion") != "v2"
            or host.get("os") != "linux" or host.get("cgroupManager") != "systemd"
            or host.get("kernel") != "6.8.0-101-generic"
            or type(host.get("security")) is not dict or host["security"].get("rootless") is not True
            or type(uid) is not int or not 0 < uid < _ID_END
            or type(gid) is not int or not 0 < gid < _ID_END):
        raise ProcessBoundaryError("L1 requires the verified rootless Podman 4.9.3 amd64 schema")
    oci, mappings = host.get("ociRuntime"), host.get("idMappings")
    if type(oci) is not dict or type(mappings) is not dict:
        raise ProcessBoundaryError("Podman runtime or outer namespace evidence is absent")
    strings = {"kernel": host.get("kernel"), "cgroup_manager": host.get("cgroupManager"),
        "oci_name": oci.get("name"), "oci_path": oci.get("path"), "oci_version": oci.get("version")}
    if any(type(value) is not str or not value or len(value) > 4096 for value in strings.values()):
        raise ProcessBoundaryError("Podman platform evidence is incomplete")
    if (oci.get("name") != "crun" or oci.get("path") != "/usr/bin/crun"
            or oci["version"].splitlines()[0] != "crun version 1.14.1"):
        raise ProcessBoundaryError("Podman OCI runtime is outside the verified platform")
    evidence = {"schema": SCHEMA, "version": "4.9.3", "arch": "amd64", "cgroup_version": "v2",
        **strings, "host_uid": uid, "host_gid": gid,
        "uid_map": info_map(mappings.get("uidmap")), "gid_map": info_map(mappings.get("gidmap"))}
    if any(host_id == 0 for key in ("uid_map", "gid_map") for _, host_id, _ in evidence[key]):
        raise ProcessBoundaryError("Rootless runtime maps host root")
    if map_id(evidence["uid_map"], 0) != uid or map_id(evidence["gid_map"], 0) != gid:
        raise ProcessBoundaryError("Rootless namespace does not belong to the service UID/GID")
    return evidence


def runtime_log_request(limit: int) -> int:
    if type(limit) is not int or not 0 < limit <= MAX_LOG_LIMIT:
        raise ProcessBoundaryError("Runtime log bounds support only positive limits up to 1 GiB")
    return min(5_000_000, max(1, limit * 95 // 100))


def log_bound(display: object, limit: int) -> dict:
    request = runtime_log_request(limit)
    if type(display) is not str or len(display) > 16:
        raise ProcessBoundaryError("Podman HumanSize log evidence must be a string")
    match = re.fullmatch(r"([1-9][0-9]*(?:\.[0-9]*[1-9])?)(B|kB|MB|GB)", display)
    if match is None:
        raise ProcessBoundaryError("Unsupported Podman HumanSize log evidence")
    digits = match[1].replace(".", "").rstrip("0")
    mantissa = Decimal(match[1])
    if len(digits) > 4 or mantissa > 1000 or (match[2] == "B" and ("." in match[1] or mantissa >= 1000)):
        raise ProcessBoundaryError("Unsupported Podman four-significant-digit SI display")
    amount = mantissa * {"B": 1, "kB": 1000, "MB": 1000**2, "GB": 1000**3}[match[2]]
    if not 0 < amount <= MAX_LOG_LIMIT:
        raise ProcessBoundaryError("Podman log evidence is outside the verified domain")
    if amount < 1000 and match[2] == "B" and "." not in match[1]:
        upper = int(amount)
    else:
        upper = int((amount * Decimal("1.001")).to_integral_value(rounding=ROUND_CEILING)) + 1
    if upper > limit:
        raise ProcessBoundaryError("Podman runtime log upper bound exceeds the frozen profile")
    return {"requested_bytes": request, "display": display, "upper_bytes": upper,
        "algorithm": LOG_BOUND_VERSION, "profile_limit": limit}
