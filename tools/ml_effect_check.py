"""Predeclared public-product effects, separate from independent quality N."""

import json

import ml_feedback_check


KINDS = {"tags", "search_items", "suggested_experts", "question_match", "routing_notifications"}
PHASES = {"initial", "before_replay", "after_actions", "after_challenge"}


def validate(case):
    actors = {post["actor"] for post in case["posts"]} | {
        actor for post in case["posts"] for actor in post.get("helped_by", [])} | ml_feedback_check.actors(case)
    for phase, checks in case.get("effects", {}).items():
        if (phase not in PHASES or (phase != "initial" and not case.get("actions"))
                or (phase == "after_challenge" and not case.get("routing_suppression"))):
            raise ValueError("Effect phases must name initial or an actual source-change phase")
        seen = set()
        for check in checks:
            kind = check["kind"]
            if kind not in KINDS:
                raise ValueError("Unknown product effect kind")
            fields = {"kind", "expected"}
            if kind in {"tags", "suggested_experts", "question_match"}:
                fields.add("post")
                index = check["post"]
                if type(index) is not int or not 0 <= index < len(case["posts"]):
                    raise ValueError("Effects must reference a case post")
                post = case["posts"][index]
                if post["visibility"] != "team" or (kind != "tags" and post["kind"] != "question"):
                    raise ValueError("Effects must reference an appropriate public post")
            if kind in {"question_match", "routing_notifications"}:
                fields.add("actor")
                if check["actor"] not in actors:
                    raise ValueError("Effects must reference a case actor")
            if kind == "search_items":
                fields.add("query")
                if not isinstance(check["query"], str) or not check["query"].strip():
                    raise ValueError("Search effects need a nonempty query")
            if set(check) != fields:
                raise ValueError("Effect check has missing or unknown fields")
            expected = check["expected"]
            if kind == "question_match":
                if type(expected) is not bool:
                    raise ValueError("Question match expectations must be booleans")
            else:
                if not isinstance(expected, list) or len(expected) != len(set(expected)):
                    raise ValueError("Effect expectations must be unique lists")
                if kind in {"search_items", "routing_notifications"}:
                    if any(type(i) is not int or not 0 <= i < len(case["posts"]) or
                           case["posts"][i]["visibility"] != "team" or
                           (kind == "routing_notifications" and case["posts"][i]["kind"] != "question")
                           for i in expected):
                        raise ValueError("Expected effects must identify appropriate public posts")
                elif any(not isinstance(s, str) or not s.strip() for s in expected):
                    raise ValueError("Named effects need nonempty labels")
                if kind == "suggested_experts" and any(actor not in actors for actor in expected):
                    raise ValueError("Suggested experts must identify case actors")
            key = identity(check)
            if key in seen:
                raise ValueError("An effect target may be declared only once per phase")
            seen.add(key)
    challenge = case.get("routing_suppression")
    if challenge is None:
        return
    if set(challenge) != {"actor", "concept", "historical_question", "challenge_question", "body"}:
        raise ValueError("Routing suppression has missing or unknown fields")
    if challenge["actor"] not in actors or not case.get("actions"):
        raise ValueError("Routing suppression needs a case actor and evidence mutation")
    for field in ("concept", "body"):
        if not isinstance(challenge[field], str) or not challenge[field].strip():
            raise ValueError("Routing suppression needs a nonempty concept and body")
    targets = [challenge[field] for field in ("historical_question", "challenge_question")]
    for index in targets:
        if (type(index) is not int or not 0 <= index < len(case["posts"])
                or case["posts"][index]["kind"] != "question"
                or case["posts"][index]["visibility"] != "team"
                or case["posts"][index]["actor"] == challenge["actor"]):
            raise ValueError("Routing suppression targets must be public questions by another actor")
    if targets[0] == targets[1] or any(action["post"] in targets for action in case["actions"]):
        raise ValueError("Routing suppression needs distinct, otherwise unchanged question targets")
    if challenge["body"] == case["posts"][targets[1]]["body"]:
        raise ValueError("Routing suppression must change the fresh target")
    # Gold history is frozen. Keeping Q1 proves retention; absence on a new Q2
    # cannot be explained by Q1's existing route deduplication key.
    histories = []
    for phase in ("initial", "after_actions", "after_challenge"):
        checks = [check for check in case.get("effects", {}).get(phase, [])
                  if check["kind"] == "routing_notifications" and check["actor"] == challenge["actor"]]
        if len(checks) != 1 or targets[0] not in checks[0]["expected"] or targets[1] in checks[0]["expected"]:
            raise ValueError("Routing suppression needs frozen retained history in all three phases")
        histories.append(checks[0]["expected"])
    if any(set(history) != set(histories[0]) for history in histories[1:]):
        raise ValueError("Routing suppression must preserve historical notifications")


def identity(check):
    return json.dumps({k: v for k, v in check.items() if k != "expected"}, sort_keys=True)


def grade(case, phase, observed, posts):
    checks = []
    post_ids = {row["id"]: index for index, row in enumerate(posts)}
    details = {row["post"]: row for row in observed["details"]}

    def detail(index, field):
        row = details[index]
        deleted = phase != "initial" and any(
            action["type"] == "delete" and action["post"] == index for action in case.get("actions", []))
        if deleted and row["status"] == 404:
            return []
        if row["status"] != 200:
            raise ValueError("Missing public detail cannot certify an empty effect")
        return row["response"][field]

    def post_index(value):
        # Unexpected public rows must remain visible to the grader.
        return post_ids.get(value, "unlabelled:" + str(value))

    for check in case.get("effects", {}).get(phase, []):
        kind = check["kind"]
        if kind == "tags":
            actual = [row["name"] for row in detail(check["post"], "concepts")]
        elif kind == "search_items":
            actual = [post_index(row["id"]) for row in observed["searches"][check["query"]]["items"]]
        elif kind == "suggested_experts":
            actual = [
                observed["actors_by_label"].get(label, "unlabelled:" + label)
                for label in detail(check["post"], "suggested_experts")]
        elif kind == "question_match":
            values = [row["matches_me"] for row in observed["questions_by_actor"][check["actor"]]
                      if row["id"] == posts[check["post"]]["id"]]
            deleted = phase != "initial" and any(action["type"] == "delete" and action["post"] == check["post"]
                                                 for action in case.get("actions", []))
            if deleted and not values:
                if details[check["post"]]["status"] != 404:
                    raise ValueError("An absent question match needs confirmed deletion")
                actual = False
            else:
                actual = values[0] if len(values) == 1 else None
        else:
            actual = [post_index(row["item_id"]) for row in
                      observed["notifications_by_actor"][check["actor"]]["notifications"]
                      if row["kind"] == "expertise_match"]
        expected = check["expected"]
        # Exact labels are deliberate: predicted aliases must not certify gold
        # downstream meaning, and duplicate notifications must fail.
        passed = (type(actual) is bool and actual == expected if kind == "question_match" else
                  sorted(actual, key=str) == sorted(expected, key=str))
        checks.append({"phase": phase, "assertion": check, "observed": actual, "passed": passed})
    return checks


def routing_suppression(case, phases, posts):
    """Recompute a fresh-target suppression proof from retained public API data."""
    challenge = case.get("routing_suppression")
    if not challenge:
        return None
    actor, concept = challenge["actor"], challenge["concept"]
    fresh = challenge["challenge_question"]
    checks = []

    def add(phase, name, actual, expected):
        checks.append({"phase": phase, "check": name, "observed": actual,
                       "expected": expected, "passed": type(actual) is type(expected) and actual == expected})

    for phase in ("initial", "after_actions", "after_challenge"):
        observed = phases[phase]["observed"]
        expertise = any(row["actor"] == actor and row["concept"] == concept for row in observed["expertise"])
        add(phase, "expertise_present", expertise, phase == "initial")
        detail = next(row for row in observed["details"] if row["post"] == fresh)
        add(phase, "challenge_question_exists", detail["status"], 200)
        add(phase, "challenge_question_open", detail["response"]["question_status"], "open")
        add(phase, "challenge_question_unanswered", detail["response"]["answers"], [])
        add(phase, "challenge_body", detail["response"]["body"],
            challenge["body"] if phase == "after_challenge" else case["posts"][fresh]["body"])
        add(phase, "challenge_topic_tagged", concept in [row["name"] for row in detail["response"]["concepts"]],
            phase == "after_challenge")
        experts = [observed["actors_by_label"].get(label, "unlabelled:" + label)
                   for label in detail["response"]["suggested_experts"]]
        add(phase, "withdrawn_expert_not_suggested", actor not in experts, True)
        matches = [row["matches_me"] for row in observed["questions_by_actor"][actor]
                   if row["id"] == posts[fresh]["id"]]
        add(phase, "challenge_not_matched", len(matches) == 1 and matches[0] is False, True)
        # Exact history, including duplicates and unexpected IDs, is checked by
        # the mandatory frozen routing_notifications assertion for each phase.
    return {"status": "PASS" if all(row["passed"] for row in checks) else "FAIL", "checks": checks}


def summarize(corpus, results):
    checks, missing, transitions, challenges = [], [], [], []
    by_id = {row["id"]: row for row in results}
    if len(by_id) != len(results) or set(by_id) - {case["id"] for case in corpus["cases"]}:
        missing.append({"reason": "Duplicate or undeclared case result"})
    for case in corpus["cases"]:
        result = by_id.get(case["id"], {})
        recorded = []
        expected = {(phase, identity(check)): check for phase, values in case.get("effects", {}).items()
                    for check in values}
        recomputed = {}
        try:
            for phase in case.get("effects", {}):
                for check in grade(case, phase, result["phases"][phase]["observed"], result["posts"]):
                    recomputed[(phase, identity(check["assertion"]))] = check
            challenge = routing_suppression(case, result.get("phases", {}), result.get("posts", []))
            if challenge:
                challenges.append({"case": case["id"], **challenge})
        except (KeyError, IndexError, TypeError, ValueError, StopIteration) as error:
            missing.append({"case": case["id"], "reason": "Missing or malformed raw API evidence: " + str(error)})
        seen = set()
        for row in result.get("effect_checks", []):
            key = row["phase"], identity(row["assertion"])
            frozen = expected.get(key)
            if frozen is None or key in seen or row["assertion"] != frozen:
                missing.append({"case": case["id"], "reason": "Undeclared, duplicated or changed effect assertion"})
                continue
            seen.add(key)
            raw = recomputed.get(key)
            if raw is None:
                missing.append({"case": case["id"], "reason": "Assertion has no complete raw API observation"})
                continue
            if row["observed"] != raw["observed"]:
                missing.append({"case": case["id"], "reason": "Recorded effect differs from raw API observation"})
            recorded.append(raw)
        checks.extend({"case": case["id"], **check} for check in recorded)
        missing.extend({"case": case["id"], "phase": phase, "target": target}
                       for phase, target in expected if (phase, target) not in seen)
        initial = {identity(c["assertion"]): c for c in recorded if c["phase"] == "initial"}
        for check in recorded:
            before = initial.get(identity(check["assertion"]))
            if check["phase"] == "initial" or before is None:
                continue
            old, new = before["assertion"]["expected"], check["assertion"]["expected"]
            removed = old is True and new is False if type(old) is bool else bool(set(old) - set(new))
            if removed:
                transitions.append({"case": case["id"], "kind": check["assertion"]["kind"],
                                    "phase": check["phase"], "demonstrated": before["passed"] and check["passed"],
                                    "meaning": ("question_delete_cleanup" if check["assertion"]["kind"] == "routing_notifications"
                                                and all(any(action["type"] == "delete" and action["post"] == index
                                                            for action in case.get("actions", [])) for index in set(old) - set(new))
                                                else "history_change" if check["assertion"]["kind"] == "routing_notifications"
                                                else "withdrawal")})
    coverage = {kind: {"positive": sum(c["assertion"]["kind"] == kind and bool(c["assertion"]["expected"])
                                      and c["passed"] for c in checks),
                       "negative": sum(c["assertion"]["kind"] == kind and not c["assertion"]["expected"]
                                      and c["passed"] for c in checks),
                       "withdrawal": sum(c["kind"] == kind and c["demonstrated"] for c in transitions)}
                for kind in sorted(KINDS)}
    del coverage["routing_notifications"]["withdrawal"]
    coverage["routing_notifications"]["fresh_target_suppression"] = sum(
        row["status"] == "PASS" and all(c["passed"] for c in checks
            if c["case"] == row["case"] and c["assertion"]["kind"] == "routing_notifications")
        for row in challenges)
    status = ("FAIL" if any(not c["passed"] for c in checks) or any(c["status"] == "FAIL" for c in challenges)
              else "INCOMPLETE" if missing else
              "NOT_DEMONSTRATED" if any(not all(row.values()) for row in coverage.values()) else "PASS")
    return {"status": status, "coverage": coverage, "missing": missing, "checks": checks,
            "transitions": transitions, "routing_suppression": challenges, "independent_quality_samples_added": 0,
            "scope": "Predeclared exact public tags, search items, suggested experts, question matches and routing "
                     "notifications. Each effect needs positive and negative checks; current effects need demonstrated "
                     "withdrawal, and routing needs a new-target suppression challenge after expertise withdrawal. "
                     "Historical notifications remain; question-delete cleanup is separate. "
                     "Counts describe engineering coverage, not independent accuracy evidence."}
