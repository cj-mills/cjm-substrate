"""The refresh verb's planning + the public-yaml projection (DEC f282571c) — pure, no pip."""

import pytest

from cjm_substrate.utils.envrefresh import (EnvPlan, checkout_dist_version, derive_public_capabilities,
                                            execute_plan, find_checkout, import_sweep, plan_env,
                                            raw_github_url, spec_floors, yaml_entry_for, yaml_specs)


def _manifest(py, env="test-x", src="/repos/cjm-capability-x/", name="cjm-capability-x"):
    return {"install": {"python_path": str(py), "conda_env": env, "package_source": src},
            "code": {"name": name, "version": "0.0.1"}}


def _checkout(root, name, version="0.0.9", static=False):
    d = root / name
    pkg = name.replace("-", "_")
    (d / pkg).mkdir(parents=True)
    if static:
        (d / "pyproject.toml").write_text(f'[project]\nname = "{name}"\nversion = "{version}"\n')
    else:
        (d / "pyproject.toml").write_text(
            f'[project]\nname = "{name}"\ndynamic = ["version"]\n'
            f'[tool.setuptools.dynamic]\nversion = {{attr = "{pkg}.__version__"}}\n')
        (d / pkg / "__init__.py").write_text(f'__version__ = "{version}"\n')
    return d


def test_dev_plan_editable_installs_every_dist_from_its_checkout(tmp_path):
    root = tmp_path / "repos"
    _checkout(root, "cjm-substrate")
    _checkout(root, "cjm-capability-x")
    py = tmp_path / "env/bin/python"
    cjm_set = {
        "cjm-capability-primitives": {"version": "0.0.9", "editable": True,
                                      "origin": str(_checkout(root, "cjm-capability-primitives"))},
        "cjm-capability-x": {"version": "0.0.1", "editable": False, "origin": ""},   # a snapshot with a checkout
        "cjm-hf-utils": {"version": "0.0.3", "editable": False, "origin": ""},       # no checkout anywhere
        "cjm-substrate": {"version": "0.0.50", "editable": False, "origin": ""},
    }
    plan = plan_env(_manifest(py), "dev", cjm_set, root=root)
    assert plan.refusal is None and plan.mode == "dev"
    targets = [c[5] for c in plan.commands if c[3:5] == ["install", "-e"]]
    assert targets == [str(root / "cjm-capability-primitives"), str(root / "cjm-capability-x"),
                       str(root / "cjm-substrate")]
    assert all("--no-deps" in c for c in plan.commands if "-e" in c)
    assert plan.skipped == ["cjm-hf-utils"]
    assert plan.commands[-1][-1] == "check" and plan.notes[-1] == "dependency check"
    assert "left as installed (no checkout): cjm-hf-utils" in plan.render()


def test_dev_plan_substrate_source_override_and_empty_env_refusal(tmp_path):
    py = tmp_path / "env/bin/python"
    alt = tmp_path / "alt-substrate"
    (alt / "pyproject.toml").parent.mkdir()
    (alt / "pyproject.toml").write_text('[project]\nname = "cjm-substrate"\nversion = "9"\n')
    plan = plan_env(_manifest(py), "dev", {"cjm-substrate": {"version": "0.0.50", "editable": False, "origin": ""}},
                    substrate_spec=str(alt))
    assert plan.commands[0][5] == str(alt)
    assert plan_env(_manifest(py), "dev", {}).refusal.startswith("the env holds no cjm-* dist")


def test_distribution_plan_one_resolve_then_the_rest(tmp_path):
    py = tmp_path / "env/bin/python"
    entry = {"name": "x", "env_name": "test-x", "package": "cjm-capability-x>=0.0.1",
             "interface_libs": ["cjm-capability-primitives>=0.0.12"],
             "adapters": [{"lib": "cjm-vad-adapter-interface>=0.0.10", "impl": "m:C"}, {"impl": "m:D"}]}
    cjm_set = {"cjm-capability-primitives": {"version": "0.0.9", "editable": False, "origin": ""},
               "cjm-capability-x": {"version": "0.0.1", "editable": False, "origin": ""},
               "cjm-hf-utils": {"version": "0.0.3", "editable": False, "origin": ""},
               "cjm-substrate": {"version": "0.0.50", "editable": False, "origin": ""},
               "cjm-vad-adapter-interface": {"version": "0.0.8", "editable": False, "origin": ""}}
    plan = plan_env(_manifest(py), "distribution", cjm_set, yaml_entry=entry, host_version="0.0.70")
    assert plan.refusal is None
    first = plan.commands[0]
    assert first[3:6] == ["install", "--upgrade", "cjm-substrate==0.0.70"]
    assert first[6:] == ["cjm-capability-primitives>=0.0.12", "cjm-vad-adapter-interface>=0.0.10",
                         "cjm-capability-x>=0.0.1"]
    assert plan.commands[1][3:] == ["install", "--upgrade", "cjm-hf-utils"]   # the rest, only-if-needed deps
    assert plan.commands[2][-1] == "check"
    assert plan.package_source == "cjm-capability-x>=0.0.1"   # re-recorded by _generate_manifest
    # an explicit substrate spec wins over the host pin
    p2 = plan_env(_manifest(py), "distribution", cjm_set, yaml_entry=entry, substrate_spec="cjm-substrate>=0.0.71")
    assert p2.commands[0][5] == "cjm-substrate>=0.0.71"


def test_distribution_plan_refuses_dev_yaml_and_missing_entry(tmp_path):
    py = tmp_path / "env/bin/python"
    dev_entry = {"env_name": "test-x", "package": "/repos/cjm-capability-x/",
                 "interface_libs": ["/repos/cjm-capability-primitives/"]}
    p = plan_env(_manifest(py), "distribution", {"cjm-substrate": {"version": "1", "editable": False, "origin": ""}},
                 yaml_entry=dev_entry)
    assert p.refusal and "names checkouts" in p.refusal and "derive-public-capabilities" in p.refusal
    assert p.commands == []
    p2 = plan_env(_manifest(py), "distribution", {}, yaml_entry=None)
    assert p2.refusal and "env_name" in p2.refusal
    assert plan_env(_manifest(py), "sideways", {}).refusal.startswith("unknown mode")


def test_execute_plan_stops_at_a_failed_install_but_runs_the_check():
    plan = EnvPlan(capability="x", conda_env="e", python_path="py", mode="dev",
                   commands=[["py", "-m", "pip", "install", "-e", "a"], ["py", "-m", "pip", "install", "-e", "b"],
                             ["py", "-m", "pip", "check"]],
                   notes=["editable a", "editable b", "dependency check"])
    calls = []

    def runner(argv, capture):
        calls.append((argv[-1], capture))
        return (1 if argv[-1] == "a" else 0), "out"
    results = execute_plan(plan, runner)
    assert [r[0] for r in results] == ["editable a"] and results[0][1] == 1
    assert calls == [("a", False)]
    ok = execute_plan(plan, lambda argv, capture: (0, "fine"))
    assert [r[1] for r in ok] == [0, 0, 0] and ok[-1] == ("dependency check", 0, "fine")


def test_yaml_entry_matches_on_env_name_and_specs_follow_install_order():
    cfg = {"capabilities": [{"name": "sys-mon-nvidia", "env_name": "test-sys-mon", "package": "p"},
                            {"name": "ffmpeg", "env_name": "test-ffmpeg", "package": "q",
                             "interface_libs": ["i"], "adapters": [{"lib": "a", "impl": "m:C"}, {"impl": "m:D"}]}]}
    assert yaml_entry_for(cfg, "test-ffmpeg")["name"] == "ffmpeg"
    assert yaml_entry_for(cfg, "nope") is None and yaml_entry_for(None, "x") is None
    assert yaml_specs(yaml_entry_for(cfg, "test-ffmpeg")) == ["i", "a", "q"]


def test_find_checkout_prefers_the_editable_origin(tmp_path):
    root = tmp_path / "repos"
    origin = _checkout(root, "cjm-elsewhere")
    assert find_checkout("cjm-x", {"editable": True, "origin": str(origin)}, root) == origin
    gone = tmp_path / "gone"
    assert find_checkout("cjm-x", {"editable": True, "origin": str(gone)}, root) is None
    _checkout(root, "cjm-x")
    assert find_checkout("cjm-x", {"editable": False, "origin": ""}, root) == root / "cjm-x"
    assert find_checkout("cjm-x", {"editable": False, "origin": ""}, None) is None


def test_import_sweep_parses_the_report_line():
    good = import_sweep("py", lambda argv, capture: (0, "noise\nIMPORT-SWEEP {\"total\": 3, \"failures\": []}\n"))
    assert good == {"total": 3, "failures": []}
    bad = import_sweep("py", lambda argv, capture: (2, "Traceback ..."))
    assert bad["total"] == 0 and "did not report" in bad["failures"][0]


def test_checkout_dist_version_static_and_dynamic(tmp_path):
    assert checkout_dist_version(_checkout(tmp_path, "cjm-dyn", "0.0.7")) == ("cjm-dyn", "0.0.7")
    assert checkout_dist_version(_checkout(tmp_path, "cjm-stat", "1.2.3", static=True)) == ("cjm-stat", "1.2.3")


def test_raw_github_url_accepts_ssh_and_https():
    want = "https://raw.githubusercontent.com/cj-mills/cjm-capability-ffmpeg/main/environment.yml"
    assert raw_github_url("git@github.com:cj-mills/cjm-capability-ffmpeg.git", "main", "environment.yml") == want
    assert raw_github_url("https://github.com/cj-mills/cjm-capability-ffmpeg", "main", "environment.yml") == want
    with pytest.raises(ValueError):
        raw_github_url("https://gitlab.com/x/y.git", "main", "environment.yml")


def test_derive_public_capabilities_projects_paths_to_specs_and_urls(tmp_path):
    root = tmp_path / "repos"
    cap = _checkout(root, "cjm-capability-ffmpeg", "0.0.27")
    (cap / "environment.yml").write_text("name: x\n")
    prim = _checkout(root, "cjm-capability-primitives", "0.0.12")
    iface = _checkout(root, "cjm-media-processing-adapter-interface", "0.0.7")
    dev = {"capabilities": [{
        "name": "ffmpeg", "env_name": "test-ffmpeg",
        "env_file": str(cap / "environment.yml"),
        "package": str(cap) + "/",
        "interface_libs": [str(prim) + "/", "cjm-already-public>=1"],
        "adapters": [{"lib": str(iface) + "/", "impl": "cjm_media_processing_adapter_interface.generic:G"},
                     {"impl": "cjm_capability_ffmpeg.extra:E"}],
    }, {"name": "bare", "env_name": "test-bare", "python_version": "3.12", "package": "cjm-bare==2.0"}]}
    remotes = {}

    def remote_of(checkout):
        remotes[checkout.name] = True
        return f"git@github.com:cj-mills/{checkout.name}.git", "main"
    public = derive_public_capabilities(dev, remote_of=remote_of)
    e = public["capabilities"][0]
    assert e["env_file"] == "https://raw.githubusercontent.com/cj-mills/cjm-capability-ffmpeg/main/environment.yml"
    assert e["package"] == "cjm-capability-ffmpeg>=0.0.27"
    assert e["interface_libs"] == ["cjm-capability-primitives>=0.0.12", "cjm-already-public>=1"]
    assert e["adapters"] == [{"lib": "cjm-media-processing-adapter-interface>=0.0.7",
                              "impl": "cjm_media_processing_adapter_interface.generic:G"},
                             {"impl": "cjm_capability_ffmpeg.extra:E"}]
    assert e["name"] == "ffmpeg" and e["env_name"] == "test-ffmpeg"
    assert public["capabilities"][1] == dev["capabilities"][1]   # already public: untouched
    assert list(remotes) == ["cjm-capability-ffmpeg"]            # only env_file needs the remote
    assert spec_floors(public) == [("cjm-already-public", "1"), ("cjm-bare", "2.0"),
                                   ("cjm-capability-ffmpeg", "0.0.27"), ("cjm-capability-primitives", "0.0.12"),
                                   ("cjm-media-processing-adapter-interface", "0.0.7")]
    bare = derive_public_capabilities(dev, remote_of=remote_of, floor=False)["capabilities"][0]
    assert bare["package"] == "cjm-capability-ffmpeg"
