from kettle.factory_check import check_factory, load_factory
from kettle.runners import branch_name, build_docker_run, build_k8s_job
from kettle.scorers import group_failures, score_criteria_met, score_tests_pass


def test_factory_loads_and_passes_checks():
    defn = load_factory("./factory")
    assert defn.factory_name == "default"
    assert check_factory(defn) == []


def test_review_vendor_must_differ():
    defn = load_factory("./factory")
    defn.agents["review"].model = defn.agents["implement"].model
    errors = check_factory(defn)
    assert any("vendor" in e for e in errors)


def test_k8s_job_spec_shape():
    spec = build_k8s_job(
        namespace="kettle",
        work_item_id="wi-abc",
        stage="building",
        agent="implement",
        model="m",
        repo="acme/app",
    )
    assert spec["kind"] == "Job"
    assert spec["spec"]["template"]["spec"]["containers"][0]["env"]
    assert "factory/wi-abc" in str(spec["spec"]["template"]["spec"]["containers"][0]["env"])


def test_branch_and_docker():
    assert branch_name("factory/", "WI_AbC!") == "factory/wi-abc"
    run = build_docker_run(
        work_item_id="a", stage="building", agent="implement", model="m", repo="r"
    )
    assert run["backend"] == "docker"


def test_scorers():
    assert score_tests_pass(0).passed is True
    assert score_tests_pass(1).passed is False
    assert score_criteria_met(3, 3, evidence_refs=3).passed is True
    assert score_criteria_met(3, 3).passed is False  # missing file:line evidence
    assert score_criteria_met(0, 0).passed is False
    assert group_failures([score_tests_pass(1), score_criteria_met(3, 3, 3)]) == "test-failures"


def test_job_name_dns_limit():
    spec = build_k8s_job(
        namespace="kettle",
        work_item_id="wi-" + "a" * 100,
        stage="building",
        agent="implement",
        model="m",
        repo="acme/app",
    )
    assert len(spec["metadata"]["name"]) <= 63


def test_repo_url_rejects_evil():
    from kettle.runners import validate_repo_url as v

    for bad in [
        "https://evil.com/a/b",
        "http://github.com/a/b",
        "git@github.com:acme/app",
        "https://github.com.evil.com/a/b",
        "a/b/../../evil",
        "not a url!!!",
    ]:
        try:
            v(bad)
            raise AssertionError(f"should raise for {bad}")
        except ValueError:
            pass
    assert v("https://github.com/acme/app") == "https://github.com/acme/app.git"
