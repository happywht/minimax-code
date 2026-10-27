"""Pinned real Podman schema and adversarial namespace/log evidence."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from minimax_code.process_runtime import (
    MAX_LOG_LIMIT,
    compose_maps,
    container_mapping,
    info_map,
    inspect_map,
    log_bound,
    normalize_map,
    proc_map,
    runtime_evidence,
    runtime_log_request,
)
from minimax_code.process_types import ProcessBoundaryError

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "podman-4.9.3-runtime.json").read_text(encoding="utf-8"))


def runtime():
    return runtime_evidence(FIXTURE["podman_info"], uid=1000, gid=1001)


def test_observed_rootless_maps_compose_to_actual_service_owner():
    evidence = runtime()
    mapping = container_mapping(FIXTURE["HostConfig"], evidence)
    assert mapping["uid_host_map"] == [[0, 100000, 1000], [1000, 1000, 1], [1001, 101000, 64536]]
    assert mapping["gid_host_map"] == [[0, 100000, 1001], [1001, 1001, 1], [1002, 101001, 64535]]
    assert mapping["fingerprint"] == container_mapping(FIXTURE["HostConfig"], evidence)["fingerprint"]
    assert evidence["kernel"] == "6.8.0-101-generic"
    assert evidence["oci_version"].splitlines()[0] == "crun version 1.14.1"


@pytest.mark.parametrize(("section", "key", "value"), [
    ("version", "Version", "4.9.4"), ("version", "Version", 4.9),
    ("host", "os", "windows"), ("host", "arch", "arm64"),
    ("host", "kernel", "6.8.0-102-generic"), ("host", "cgroupVersion", "v1"),
    ("host", "cgroupManager", "cgroupfs"), ("host", "security", []),
    ("host", "security", {"rootless": 1}), ("host", "idMappings", None),
    ("host", "ociRuntime", {"name": "crun", "path": "/usr/bin/crun", "version": "crun version 1.14.10"}),
    ("host", "ociRuntime", {"name": "runc", "path": "/usr/bin/runc", "version": "runc version 1.14.1"}),
    ("host", "ociRuntime", {"name": "crun", "path": "/tmp/crun", "version": "crun version 1.14.1"}),
])
def test_unknown_platform_schema_is_not_accepted(section, key, value):
    data = copy.deepcopy(FIXTURE["podman_info"])
    data[section][key] = value
    with pytest.raises(ProcessBoundaryError):
        runtime_evidence(data, uid=1000, gid=1001)


@pytest.mark.parametrize("data", [None, [], "4.9.3", {"host": []}, {"host": {}}])
def test_nonobject_or_missing_info_is_rejected(data):
    with pytest.raises(ProcessBoundaryError):
        runtime_evidence(data, uid=1000, gid=1001)


@pytest.mark.parametrize(("uid", "gid"), [(0, 1001), (True, 1001), (1000, False), (1000, 0), (2**32-1, 1001), (1000, 1002)])
def test_runtime_identity_requires_exact_nonroot_mapped_ids(uid, gid):
    with pytest.raises(ProcessBoundaryError):
        runtime_evidence(FIXTURE["podman_info"], uid=uid, gid=gid)


@pytest.mark.parametrize("rows", [
    None, [], {}, [[0, 0]], [[True, 1000, 1]], [[0, 1.0, 1]], [[0, 1000, False]],
    [[-1, 1000, 1]], [[0, -1, 1]], [[0, 1000, 0]], [[0, 1000, -1]],
    [[2**32-1, 1, 1]], [[0, 2**32-1, 1]], [[2**32-2, 1, 2]],
    [[0, 1000, 2], [1, 2000, 1]], [[0, 1000, 2], [2, 1001, 1]],
    [[0, 1, 1]] * 341,
])
def test_intervals_reject_wrong_type_overlap_and_uid_sentinel(rows):
    with pytest.raises(ProcessBoundaryError):
        normalize_map(rows)


def test_interval_order_and_adjacent_splits_normalize_identically():
    assert normalize_map([[2, 102, 2], [0, 100, 2]]) == [[0, 100, 4]]
    assert normalize_map([[0, 100, 1], [1, 500, 1]]) == [[0, 100, 1], [1, 500, 1]]
    assert compose_maps([[0, 0, 4]], [[2, 102, 2], [0, 100, 2]]) == [[0, 100, 4]]


def test_outer_mapping_must_cover_entire_inner_interval():
    with pytest.raises(ProcessBoundaryError, match="cover"):
        compose_maps([[0, 0, 4]], [[0, 100, 2], [3, 200, 1]])


@pytest.mark.parametrize(("parser", "value"), [
    (info_map, [{"container_id": 0, "host_id": 1000, "size": 1, "extra": 1}]),
    (info_map, [{"container_id": "0", "host_id": 1000, "size": 1}]),
    (inspect_map, ["0:1:1:2"]), (inspect_map, [" 0:1:1"]),
    (inspect_map, [[0, 1, 1]]), (inspect_map, ["9" * 100 + ":1:1"]),
    (proc_map, "0 1000 1\n\n"), (proc_map, "0:1000:1"),
    (proc_map, "0 1000 1 extra"), pytest.param(proc_map, "x" * 32769, id="oversized-kernel-map"),
])
def test_map_schema_is_not_coerced(parser, value):
    with pytest.raises(ProcessBoundaryError):
        parser(value)


@pytest.mark.parametrize("kind", ["UidMap", "GidMap"])
def test_container_owner_remap_or_missing_root_is_rejected(kind):
    host = copy.deepcopy(FIXTURE["HostConfig"])
    host["IDMappings"][kind] = ["1000:1:1"]
    with pytest.raises(ProcessBoundaryError):
        container_mapping(host, runtime())


def test_host_root_in_any_outer_mapping_is_rejected():
    data = copy.deepcopy(FIXTURE["podman_info"])
    data["host"]["idMappings"]["uidmap"][1]["host_id"] = 0
    with pytest.raises(ProcessBoundaryError):
        runtime_evidence(data, uid=1000, gid=1001)


@pytest.mark.parametrize(("display", "limit", "upper"), [
    ("1B", 1, 1), ("999B", 999, 999), ("1kB", 1002, 1002),
    ("1000kB", 1001001, 1001001), ("1MB", 1001001, 1001001),
    ("5MB", 5 * 1024**2, 5005001), ("1GB", MAX_LOG_LIMIT, 1001000001),
])
def test_conservative_si_bound_and_frozen_profile(display, limit, upper):
    proof = log_bound(display, limit)
    assert proof == {"requested_bytes": runtime_log_request(limit), "display": display,
        "upper_bytes": upper, "algorithm": "human-size-4sig-si-upper-v1", "profile_limit": limit}
    assert proof["upper_bytes"] <= limit
    assert runtime_log_request(5 * 1024**2) == 4980736


@pytest.mark.parametrize("display", [
    None, 5000000, True, "", "0B", "-1B", "NaNMB", "InfinityMB", "1e3B", "5MiB", "5 MB",
    "01MB", "1.000MB", "1.2345MB", "0.5MB", "1000B", "999.5B", "1001kB", "1.074GB",
    "1" * 100 + "B", "5MB\n", "5mb",
])
def test_unknown_or_out_of_domain_log_display_is_rejected(display):
    with pytest.raises(ProcessBoundaryError):
        log_bound(display, MAX_LOG_LIMIT)


@pytest.mark.parametrize("limit", [0, -1, True, 1.0, None, MAX_LOG_LIMIT + 1])
def test_runtime_log_request_does_not_expand_or_coerce_profiles(limit):
    with pytest.raises(ProcessBoundaryError):
        runtime_log_request(limit)


def test_rounded_old_full_cap_exceeds_profile_but_reduced_request_has_headroom():
    with pytest.raises(ProcessBoundaryError, match="upper bound"):
        log_bound("5.243MB", 5 * 1024**2)
    assert log_bound("5MB", 5 * 1024**2)["upper_bytes"] == 5005001
    assert runtime_log_request(999) == 949
    assert runtime_log_request(1000) == 950
    assert runtime_log_request(MAX_LOG_LIMIT) == 5000000
