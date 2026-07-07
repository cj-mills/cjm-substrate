"""Adapter-manifest matcher tests (projected from nbs/core/adapter_manifest.ipynb
cell 9f8a656f at the golden-reference flip) — including THE negative check
(a mismatched pairing must say no, legibly)."""

from cjm_substrate.core.adapter_manifest import (AdapterManifest, adapter_manifest_from_dict,
                                                 is_adapter_manifest,
                                                 match_protocol_against_surface)

_SURFACE = {
    "methods": [
        {"name": "add_nodes", "signature": "(self, nodes)", "params": ["nodes"]},
        {"name": "query_nodes", "signature": "(self, query)", "params": ["query"]},
        {"name": "get_context", "signature": "(self, node_id, depth=1, filter_labels=None)",
         "params": ["node_id", "depth", "filter_labels"]},
    ],
    "properties": ["name", "version"],
    "attributes": [],
}


def test_exact_prefix_and_property_matches_are_compatible():
    proto = {"methods": [{"name": "add_nodes", "params": ["nodes"]},
                         {"name": "get_context", "params": ["node_id", "depth"]}],
             "properties": ["name"]}
    v = match_protocol_against_surface(proto, _SURFACE)
    assert v["compatible"], v


def test_missing_method_says_no_legibly():
    proto = {"methods": [{"name": "transcribe", "params": ["audio"]}], "properties": []}
    v = match_protocol_against_surface(proto, _SURFACE)
    assert not v["compatible"] and v["missing_methods"] == ["transcribe"], v


def test_reordered_params_are_a_mismatch():
    proto = {"methods": [{"name": "get_context", "params": ["depth", "node_id"]}],
             "properties": []}
    v = match_protocol_against_surface(proto, _SURFACE)
    assert not v["compatible"] and v["param_mismatches"][0]["method"] == "get_context", v


def test_missing_property_is_a_mismatch():
    v = match_protocol_against_surface({"methods": [], "properties": ["task_count"]},
                                       _SURFACE)
    assert not v["compatible"] and v["missing_properties"] == ["task_count"], v


def test_pre_fracture_surface_is_not_compatible_with_reason():
    # No recorded surface -> NOT compatible; staleness stays visible instead of
    # silently mis-answering the compatibility query
    proto = {"methods": [{"name": "add_nodes", "params": ["nodes"]}], "properties": []}
    v = match_protocol_against_surface(proto, None)
    assert not v["compatible"] and "structural_surface" in v["reason"], v


def test_param_less_old_format_falls_back_to_name_only():
    old_surface = {"methods": [{"name": "add_nodes", "signature": "(...)"}], "properties": []}
    v = match_protocol_against_surface({"methods": [{"name": "add_nodes", "params": ["nodes"]}],
                                        "properties": []}, old_surface)
    assert v["compatible"], v


def test_manifest_round_trip_and_kind_check():
    am = AdapterManifest(
        name="cjm_graph_storage_adapter_interface.generic.GenericGraphStorageAdapter",
        version="0.0.1", task_name="graph-storage",
        module="cjm_graph_storage_adapter_interface.generic",
        class_name="GenericGraphStorageAdapter",
        required_tool_protocol="cjm_graph_storage_adapter_interface.adapter.GraphStorageToolProtocol",
        protocol_members={"methods": [{"name": "add_nodes", "params": ["nodes"]}],
                          "properties": ["name"]})
    d = am.to_dict()
    assert is_adapter_manifest(d) and d["class"] == "GenericGraphStorageAdapter"
    assert adapter_manifest_from_dict(d) == am
    assert not is_adapter_manifest({"name": "x"})  # capability manifests lack "unit"
