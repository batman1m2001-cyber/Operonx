"""Tests for BranchNode - conditional routing node."""

import pytest
from hush.core.nodes.flow.branch_node import BranchNode, Branch, if_
from hush.core.nodes.transform.code_node import CodeNode
from hush.core.nodes.graph.graph_node import GraphNode
from hush.core.nodes.base import START, END, PARENT
from hush.core.states import StateSchema, MemoryState
from hush.core.states.ref import Ref


# ============================================================
# Test 1: Basic Score Routing
# ============================================================

class TestBasicScoreRouting:
    """Test basic conditional routing based on score."""

    @pytest.fixture
    def score_graph(self):
        """Create a score routing graph."""
        with GraphNode(name="score_workflow") as graph:
            # Create actual target nodes
            excellent = CodeNode(
                name="excellent",
                code_fn=lambda: {"grade": "A"},
                inputs={},
                outputs={"*": PARENT}
            )
            good = CodeNode(
                name="good",
                code_fn=lambda: {"grade": "B"},
                inputs={},
                outputs={"*": PARENT}
            )
            pass_node = CodeNode(
                name="pass",
                code_fn=lambda: {"grade": "C"},
                inputs={},
                outputs={"*": PARENT}
            )
            fail = CodeNode(
                name="fail",
                code_fn=lambda: {"grade": "F"},
                inputs={},
                outputs={"*": PARENT}
            )

            branch = BranchNode(
                name="router",
                cases=[
                    (PARENT["score"] >= 90, "excellent"),
                    (PARENT["score"] >= 70, "good"),
                    (PARENT["score"] >= 50, "pass"),
                ],
                default="fail",
            )
            START >> branch
            branch >> [excellent, good, pass_node, fail]
            [excellent, good, pass_node, fail] >> END

        graph.build()
        return graph, branch

    @pytest.mark.asyncio
    async def test_score_85_routes_to_good(self, score_graph):
        """Test score=85 routes to 'good'."""
        graph, branch = score_graph
        schema = StateSchema(graph)
        state = MemoryState(schema, inputs={"score": 85})

        result = await branch.run(state)
        assert result["target"] == "good"

    @pytest.mark.asyncio
    async def test_score_95_routes_to_excellent(self, score_graph):
        """Test score=95 routes to 'excellent'."""
        graph, branch = score_graph
        schema = StateSchema(graph)
        state = MemoryState(schema, inputs={"score": 95})

        result = await branch.run(state)
        assert result["target"] == "excellent"

    @pytest.mark.asyncio
    async def test_score_30_routes_to_default(self, score_graph):
        """Test score=30 routes to default 'fail'."""
        graph, branch = score_graph
        schema = StateSchema(graph)
        state = MemoryState(schema, inputs={"score": 30})

        result = await branch.run(state)
        assert result["target"] == "fail"

    @pytest.mark.asyncio
    async def test_state_is_updated(self, score_graph):
        """Test that state is updated with routing result."""
        graph, branch = score_graph
        schema = StateSchema(graph)
        state = MemoryState(schema, inputs={"score": 85})

        await branch.run(state)
        assert state["score_workflow.router", "target", None] == "good"

    @pytest.mark.asyncio
    async def test_get_target_method(self, score_graph):
        """Test get_target method returns correct value."""
        graph, branch = score_graph
        schema = StateSchema(graph)
        state = MemoryState(schema, inputs={"score": 85})

        await branch.run(state)
        assert branch.get_target(state) == "good"


# ============================================================
# Test 2: Multiple Variables with Refs
# ============================================================

class TestMultipleVariablesRouting:
    """Test routing with multiple variables from refs."""

    @pytest.fixture
    def user_routing_graph(self):
        """Create a user routing graph with multiple conditions."""
        with GraphNode(name="user_workflow") as graph:
            user_data = CodeNode(
                name="user_data",
                code_fn=lambda: {"age": 25, "verified": True},
                inputs={}
            )

            # Create actual target nodes
            adult_verified = CodeNode(
                name="adult_verified",
                code_fn=lambda: {"status": "adult"},
                inputs={},
                outputs={"*": PARENT}
            )
            teen = CodeNode(
                name="teen",
                code_fn=lambda: {"status": "teen"},
                inputs={},
                outputs={"*": PARENT}
            )
            child = CodeNode(
                name="child",
                code_fn=lambda: {"status": "child"},
                inputs={},
                outputs={"*": PARENT}
            )

            branch = BranchNode(
                name="user_router",
                cases=[
                    (user_data["age"] >= 18, "adult_verified"),
                    (user_data["age"] >= 13, "teen"),
                ],
                default="child",
                inputs={
                    "age": user_data["age"],
                }
            )

            START >> user_data >> branch
            branch >> [adult_verified, teen, child]
            [adult_verified, teen, child] >> END

        graph.build()
        return graph, branch

    @pytest.mark.asyncio
    async def test_adult_verified_routing(self, user_routing_graph):
        """Test age=25 routes to adult_verified."""
        graph, branch = user_routing_graph
        schema = StateSchema(graph)
        state = MemoryState(schema)

        state["user_workflow.user_data", "age", None] = 25

        result = await branch.run(state)
        assert result["target"] == "adult_verified"

    @pytest.mark.asyncio
    async def test_teen_routing(self, user_routing_graph):
        """Test age=15 routes to teen."""
        graph, branch = user_routing_graph
        schema = StateSchema(graph)
        state = MemoryState(schema)

        state["user_workflow.user_data", "age", None] = 15

        result = await branch.run(state)
        assert result["target"] == "teen"


# ============================================================
# Test 3: Anchor Override
# ============================================================

class TestAnchorOverride:
    """Test anchor parameter overriding conditions."""

    def test_anchor_overrides_condition(self):
        """Test that anchor overrides normal condition evaluation."""
        branch = BranchNode(
            name="anchor_router",
            cases=[
                (PARENT["status"] == "active", "process"),
            ],
            default="skip",
        )

        result = branch(status="active", anchor="force_target")
        assert result["target"] == "force_target"
        assert result["matched"] == "anchor"

    def test_without_anchor_condition_works(self):
        """Test normal condition when anchor is None."""
        branch = BranchNode(
            name="anchor_router",
            cases=[
                (PARENT["status"] == "active", "process"),
            ],
            default="skip",
        )

        result = branch(status="active", anchor=None)
        assert result["target"] == "process"


# ============================================================
# Test 4: Schema Extraction
# ============================================================

class TestBranchSchemaExtraction:
    """Test automatic schema extraction from cases."""

    def test_inputs_from_conditions(self):
        """Test that inputs are extracted from condition variables."""
        branch = BranchNode(
            name="test",
            cases=[
                (PARENT["score"] >= 90, "excellent"),
            ],
            default="fail"
        )
        assert "score" in branch.inputs
        assert "anchor" in branch.inputs  # anchor is always present

    def test_outputs_have_target_and_matched(self):
        """Test that outputs include target and matched."""
        branch = BranchNode(
            name="test",
            cases=[(PARENT["x"] > 0, "positive")],
            default="zero"
        )
        assert "target" in branch.outputs
        assert "matched" in branch.outputs

    def test_multiple_condition_variables(self):
        """Test extraction of multiple variables from conditions."""
        branch = BranchNode(
            name="test",
            cases=[
                (PARENT["age"] >= 18, "adult"),
            ],
            default="child"
        )
        assert "age" in branch.inputs


# ============================================================
# Test 5: Quick __call__ Test
# ============================================================

class TestBranchQuickCall:
    """Test direct __call__ invocation."""

    def test_positive_routing(self):
        """Test routing to positive."""
        branch = BranchNode(
            name="quick",
            cases=[
                (PARENT["x"] > 0, "positive"),
                (PARENT["x"] < 0, "negative"),
            ],
            default="zero"
        )
        result = branch(x=5)
        assert result["target"] == "positive"

    def test_negative_routing(self):
        """Test routing to negative."""
        branch = BranchNode(
            name="quick",
            cases=[
                (PARENT["x"] > 0, "positive"),
                (PARENT["x"] < 0, "negative"),
            ],
            default="zero"
        )
        result = branch(x=-3)
        assert result["target"] == "negative"

    def test_default_routing(self):
        """Test routing to default."""
        branch = BranchNode(
            name="quick",
            cases=[
                (PARENT["x"] > 0, "positive"),
                (PARENT["x"] < 0, "negative"),
            ],
            default="zero"
        )
        result = branch(x=0)
        assert result["target"] == "zero"


# ============================================================
# Test 6: Candidates Property
# ============================================================

class TestBranchCandidates:
    """Test candidates property."""

    def test_candidates_from_cases(self):
        """Test that candidates are derived from cases and default."""
        branch = BranchNode(
            name="test",
            cases=[
                (PARENT["x"] > 0, "positive"),
                (PARENT["x"] < 0, "negative"),
            ],
            default="zero"
        )
        candidates = branch.candidates
        assert "positive" in candidates
        assert "negative" in candidates
        assert "zero" in candidates

    def test_explicit_candidates(self):
        """Test explicit candidates override."""
        branch = BranchNode(
            name="test",
            cases=[(PARENT["x"] > 0, "a")],
            default="b",
            candidates=["c", "d", "e"]
        )
        assert branch.candidates == ["c", "d", "e"]


# ============================================================
# Test 7: Fluent Branch Builder Syntax
# ============================================================

class TestBranchFluentBuilder:
    """Test fluent Branch builder với if_/else_ syntax."""

    def test_basic_fluent_syntax(self):
        """Test basic fluent syntax: Branch().if_().else_()"""
        branch = (Branch("router")
            .if_(PARENT["score"] >= 90, "excellent")
            .if_(PARENT["score"] >= 70, "good")
            .if_(PARENT["score"] >= 50, "pass")
            .else_("fail"))

        # Verify it's a BranchNode
        assert isinstance(branch, BranchNode)
        assert branch.name == "router"

        # Verify cases were created
        assert len(branch.cases) == 3
        assert branch.default == "fail"

        # Test routing
        result = branch(score=95)
        assert result["target"] == "excellent"

        result = branch(score=75)
        assert result["target"] == "good"

        result = branch(score=55)
        assert result["target"] == "pass"

        result = branch(score=30)
        assert result["target"] == "fail"

    def test_comparison_operators(self):
        """Test various comparison operators in conditions."""
        branch = (Branch("comparisons")
            .if_(PARENT["x"] == 0, "zero")
            .if_(PARENT["x"] < 0, "negative")
            .if_(PARENT["x"] > 100, "large")
            .if_(PARENT["x"] <= 10, "small")
            .if_(PARENT["x"] >= 50, "medium_large")
            .else_("medium"))

        assert branch(x=0)["target"] == "zero"
        assert branch(x=-5)["target"] == "negative"
        assert branch(x=150)["target"] == "large"
        assert branch(x=5)["target"] == "small"
        assert branch(x=75)["target"] == "medium_large"
        assert branch(x=30)["target"] == "medium"

    def test_fluent_with_node_targets(self):
        """Test fluent syntax with node objects as targets."""
        with GraphNode(name="fluent_graph") as graph:
            excellent = CodeNode(
                name="excellent",
                code_fn=lambda: {"grade": "A"},
                inputs={}
            )
            good = CodeNode(
                name="good",
                code_fn=lambda: {"grade": "B"},
                inputs={}
            )
            fail = CodeNode(
                name="fail",
                code_fn=lambda: {"grade": "F"},
                inputs={}
            )

            branch = (Branch("grader")
                .if_(PARENT["score"] >= 90, excellent)
                .if_(PARENT["score"] >= 70, good)
                .else_(fail))

            START >> branch
            branch >> [excellent, good, fail]
            [excellent, good, fail] >> END

        graph.build()

        # Verify node names were extracted
        assert "excellent" in branch.candidates
        assert "good" in branch.candidates
        assert "fail" in branch.candidates

    def test_fluent_without_else_(self):
        """Test fluent syntax without default (using build())."""
        branch = (Branch("no_default")
            .if_(PARENT["status"] == "active", "process")
            .if_(PARENT["status"] == "pending", "queue")
            .build())

        assert isinstance(branch, BranchNode)
        assert branch.default is None

        result = branch(status="active")
        assert result["target"] == "process"

        result = branch(status="pending")
        assert result["target"] == "queue"

        # Unknown status returns None
        result = branch(status="unknown")
        assert result["target"] is None

    @pytest.mark.asyncio
    async def test_fluent_in_graph_execution(self):
        """Test fluent Branch in full graph execution."""
        with GraphNode(name="fluent_workflow") as graph:
            branch = (Branch("scorer")
                .if_(PARENT["score"] >= 90, "excellent")
                .if_(PARENT["score"] >= 70, "good")
                .else_("fail"))

            excellent = CodeNode(
                name="excellent",
                code_fn=lambda: {"result": "A grade!"},
                inputs={},
                outputs={"*": PARENT}
            )
            good = CodeNode(
                name="good",
                code_fn=lambda: {"result": "B grade!"},
                inputs={},
                outputs={"*": PARENT}
            )
            fail = CodeNode(
                name="fail",
                code_fn=lambda: {"result": "Try again!"},
                inputs={},
                outputs={"*": PARENT}
            )

            # Each branch target goes to END independently
            START >> branch
            branch >> [excellent, good, fail]
            [excellent, good, fail] >> END

        graph.build()
        schema = StateSchema(graph)

        # Test score 95 → excellent
        state = schema.create_state(inputs={"score": 95})
        result = await graph.run(state)
        assert result["result"] == "A grade!"

        # Test score 75 → good
        state = schema.create_state(inputs={"score": 75})
        result = await graph.run(state)
        assert result["result"] == "B grade!"

        # Test score 40 → fail
        state = schema.create_state(inputs={"score": 40})
        result = await graph.run(state)
        assert result["result"] == "Try again!"

    def test_fluent_inputs_are_refs(self):
        """Test that fluent builder correctly creates Ref inputs."""
        branch = (Branch("ref_test")
            .if_(PARENT["value"] >= 100, "high")
            .else_("low"))

        # Verify input is a Ref
        assert "value" in branch.inputs
        assert isinstance(branch.inputs["value"].value, Ref)

    def test_fluent_ne_operator(self):
        """Test != operator in fluent syntax."""
        branch = (Branch("ne_test")
            .if_(PARENT["status"] != "disabled", "active")
            .else_("inactive"))

        assert branch(status="enabled")["target"] == "active"
        assert branch(status="disabled")["target"] == "inactive"

    def test_fluent_with_apply(self):
        """Test fluent syntax with .apply() for custom functions."""
        def is_high_score(score):
            return score >= 90

        def is_passing(score):
            return score >= 50

        branch = (Branch("apply_router")
            .if_(PARENT["score"].apply(is_high_score), "excellent")
            .if_(PARENT["score"].apply(is_passing), "pass")
            .else_("fail"))

        # Test routing with custom functions
        assert branch(score=95)["target"] == "excellent"
        assert branch(score=75)["target"] == "pass"
        assert branch(score=30)["target"] == "fail"

    def test_fluent_apply_with_args(self):
        """Test .apply() with additional arguments."""
        def is_above_threshold(value, threshold):
            return value > threshold

        branch = (Branch("threshold_router")
            .if_(PARENT["value"].apply(is_above_threshold, 100), "high")
            .if_(PARENT["value"].apply(is_above_threshold, 50), "medium")
            .else_("low"))

        assert branch(value=150)["target"] == "high"
        assert branch(value=75)["target"] == "medium"
        assert branch(value=25)["target"] == "low"

    def test_fluent_apply_with_builtin_functions(self):
        """Test .apply() with builtin functions like len."""
        branch = (Branch("len_router")
            .if_(PARENT["x"].apply(len) > 5, "long")
            .if_(PARENT["x"].apply(len) > 2, "medium")
            .else_("short"))

        assert branch(x="hello!")["target"] == "long"  # len=6
        assert branch(x="hello")["target"] == "medium"  # len=5
        assert branch(x="hi")["target"] == "short"  # len=2
        assert branch(x=[1, 2, 3, 4, 5, 6])["target"] == "long"  # len=6


# ============================================================
# Test 8: if_() Shorthand
# ============================================================

class TestIfShorthand:
    """Test if_() top-level function shorthand."""

    def test_if_else_basic(self):
        """Test basic if_().else_() syntax."""
        router = if_(PARENT["x"] > 0, "positive").else_("negative")

        assert isinstance(router, BranchNode)
        assert router.name == "router"
        assert router(x=5)["target"] == "positive"
        assert router(x=-1)["target"] == "negative"

    def test_if_chain(self):
        """Test chained if_().if_().else_() syntax."""
        grader = (if_(PARENT["score"] >= 90, "excellent")
                  .if_(PARENT["score"] >= 70, "good")
                  .if_(PARENT["score"] >= 50, "pass")
                  .else_("fail"))

        assert grader.name == "grader"
        assert grader(score=95)["target"] == "excellent"
        assert grader(score=75)["target"] == "good"
        assert grader(score=55)["target"] == "pass"
        assert grader(score=30)["target"] == "fail"

    def test_if_build_no_default(self):
        """Test if_() with .build() instead of .else_()."""
        checker = (if_(PARENT["status"] == "active", "process")
                   .if_(PARENT["status"] == "pending", "queue")
                   .build())

        assert checker.name == "checker"
        assert checker(status="active")["target"] == "process"
        assert checker(status="pending")["target"] == "queue"
        assert checker(status="unknown")["target"] is None

    def test_branch_explicit_name_still_works(self):
        """Test Branch('name') with explicit name."""
        router = Branch("custom_name").if_(PARENT["x"] > 0, "pos").else_("neg")

        assert router.name == "custom_name"
        assert router(x=5)["target"] == "pos"

    def test_branch_no_name_auto_infers(self):
        """Test Branch() without name auto-infers from variable."""
        my_branch = Branch().if_(PARENT["x"] > 0, "pos").else_("neg")

        assert my_branch.name == "my_branch"
        assert my_branch(x=5)["target"] == "pos"

    def test_if_with_apply(self):
        """Test if_() with .apply() conditions."""
        length_check = (if_(PARENT["text"].apply(len) > 10, "long")
                        .else_("short"))

        assert length_check.name == "length_check"
        assert length_check(text="hello world!!")["target"] == "long"
        assert length_check(text="hi")["target"] == "short"

    @pytest.mark.asyncio
    async def test_if_in_graph_execution(self):
        """Test if_() shorthand in full graph execution."""
        with GraphNode(name="if_workflow") as graph:
            branch = (if_(PARENT["score"] >= 70, "pass_node")
                      .else_("fail_node"))

            pass_node = CodeNode(
                name="pass_node",
                code_fn=lambda: {"result": "Passed!"},
                inputs={},
                outputs={"*": PARENT}
            )
            fail_node = CodeNode(
                name="fail_node",
                code_fn=lambda: {"result": "Failed!"},
                inputs={},
                outputs={"*": PARENT}
            )

            START >> branch
            branch >> [pass_node, fail_node]
            [pass_node, fail_node] >> END

        graph.build()
        schema = StateSchema(graph)

        state = schema.create_state(inputs={"score": 80})
        result = await graph.run(state)
        assert result["result"] == "Passed!"

        state = schema.create_state(inputs={"score": 50})
        result = await graph.run(state)
        assert result["result"] == "Failed!"


# ============================================================
# Test: Local Variable Pattern for Cleaner Syntax
# ============================================================

class TestLocalVariablePattern:
    """Test using local variables for cleaner branch conditions."""

    def test_local_variable_basic(self):
        """Test basic local variable pattern."""
        # Simulate: call_type = task_classifier["call_type"]
        mock_node = type('MockNode', (), {'full_name': 'task_classifier'})()
        call_type = Ref(mock_node, "call_type")

        router = (if_(call_type == "PRE_DUE_REMINDER", "nyd_raba_detector")
                  .if_(call_type == "OVERDUE_REMINDER", "ovd_raba_detector")
                  .if_(call_type == "NO_REMINDER", "no_reminder_violation")
                  .else_("non_violation"))

        # Verify it's a BranchNode
        assert isinstance(router, BranchNode)
        assert len(router.cases) == 3
        assert router.default == "non_violation"

    def test_local_variable_quick_call_pre_due(self):
        """Test local variable pattern with quick call - PRE_DUE_REMINDER."""
        mock_node = type('MockNode', (), {'full_name': 'task_classifier'})()
        call_type = Ref(mock_node, "call_type")

        router = (if_(call_type == "PRE_DUE_REMINDER", "nyd_raba_detector")
                  .if_(call_type == "OVERDUE_REMINDER", "ovd_raba_detector")
                  .if_(call_type == "NO_REMINDER", "no_reminder_violation")
                  .else_("non_violation"))

        result = router(call_type="PRE_DUE_REMINDER")
        assert result["target"] == "nyd_raba_detector"

    def test_local_variable_quick_call_overdue(self):
        """Test local variable pattern with quick call - OVERDUE_REMINDER."""
        mock_node = type('MockNode', (), {'full_name': 'task_classifier'})()
        call_type = Ref(mock_node, "call_type")

        router = (if_(call_type == "OVERDUE_REMINDER", "ovd_raba_detector")
                  .if_(call_type == "NO_REMINDER", "no_reminder_violation")
                  .else_("non_violation"))

        result = router(call_type="OVERDUE_REMINDER")
        assert result["target"] == "ovd_raba_detector"

    def test_local_variable_quick_call_no_reminder(self):
        """Test local variable pattern with quick call - NO_REMINDER."""
        mock_node = type('MockNode', (), {'full_name': 'task_classifier'})()
        call_type = Ref(mock_node, "call_type")

        router = (if_(call_type == "PRE_DUE_REMINDER", "nyd_raba_detector")
                  .if_(call_type == "OVERDUE_REMINDER", "ovd_raba_detector")
                  .if_(call_type == "NO_REMINDER", "no_reminder_violation")
                  .else_("non_violation"))

        result = router(call_type="NO_REMINDER")
        assert result["target"] == "no_reminder_violation"

    def test_local_variable_quick_call_default(self):
        """Test local variable pattern with quick call - default case."""
        mock_node = type('MockNode', (), {'full_name': 'task_classifier'})()
        call_type = Ref(mock_node, "call_type")

        router = (if_(call_type == "PRE_DUE_REMINDER", "nyd_raba_detector")
                  .if_(call_type == "OVERDUE_REMINDER", "ovd_raba_detector")
                  .if_(call_type == "NO_REMINDER", "no_reminder_violation")
                  .else_("non_violation"))

        result = router(call_type="UNKNOWN_TYPE")
        assert result["target"] == "non_violation"

    def test_multiple_local_variables(self):
        """Test with multiple local variable aliases."""
        mock_node = type('MockNode', (), {'full_name': 'classifier'})()
        call_type = Ref(mock_node, "call_type")
        is_active = Ref(mock_node, "is_active")

        # Using compound conditions with local variables
        router = (if_((call_type == "REMINDER") & is_active, "active_reminder")
                  .if_(call_type == "REMINDER", "inactive_reminder")
                  .else_("other"))

        # Test active reminder
        result = router(call_type="REMINDER", is_active=True)
        assert result["target"] == "active_reminder"

        # Test inactive reminder
        result = router(call_type="REMINDER", is_active=False)
        assert result["target"] == "inactive_reminder"

        # Test other
        result = router(call_type="OTHER", is_active=True)
        assert result["target"] == "other"


# ============================================================
# Test: Compound Boolean Operations in Branch
# ============================================================

class TestCompoundBooleanInBranch:
    """Test compound boolean operations (& and |) in BranchNode."""

    def test_and_condition(self):
        """Test & condition in branch."""
        mock_node = type('MockNode', (), {'full_name': 'data'})()
        score = Ref(mock_node, "score")
        status = Ref(mock_node, "status")

        router = (if_((score >= 90) & (status == "active"), "excellent_active")
                  .if_(score >= 90, "excellent_inactive")
                  .else_("other"))

        # Both conditions true
        result = router(score=95, status="active")
        assert result["target"] == "excellent_active"

        # First true, second false
        result = router(score=95, status="inactive")
        assert result["target"] == "excellent_inactive"

        # First false
        result = router(score=50, status="active")
        assert result["target"] == "other"

    def test_or_condition(self):
        """Test | condition in branch."""
        mock_node = type('MockNode', (), {'full_name': 'data'})()
        is_vip = Ref(mock_node, "is_vip")
        score = Ref(mock_node, "score")

        router = (if_(is_vip | (score >= 90), "priority")
                  .else_("normal"))

        # is_vip true
        result = router(is_vip=True, score=50)
        assert result["target"] == "priority"

        # score >= 90
        result = router(is_vip=False, score=95)
        assert result["target"] == "priority"

        # Both false
        result = router(is_vip=False, score=50)
        assert result["target"] == "normal"

    def test_not_condition(self):
        """Test ~ (not) condition in branch."""
        mock_node = type('MockNode', (), {'full_name': 'data'})()
        is_disabled = Ref(mock_node, "is_disabled")

        router = (if_(~is_disabled, "enabled")
                  .else_("disabled"))

        result = router(is_disabled=False)
        assert result["target"] == "enabled"

        result = router(is_disabled=True)
        assert result["target"] == "disabled"

    def test_complex_compound(self):
        """Test complex compound: (a & b) | c."""
        mock_node = type('MockNode', (), {'full_name': 'data'})()
        closed_by = Ref(mock_node, "closed_by")
        customer_silent = Ref(mock_node, "customer_silent")
        force_flag = Ref(mock_node, "force_flag")

        # (closed_by == 'AGENT' & customer_silent) | force_flag
        router = (if_(((closed_by == "AGENT") & customer_silent) | force_flag, "special_case")
                  .else_("normal_case"))

        # First part true: closed_by=AGENT and customer_silent=True
        result = router(closed_by="AGENT", customer_silent=True, force_flag=False)
        assert result["target"] == "special_case"

        # Second part true: force_flag=True
        result = router(closed_by="CUSTOMER", customer_silent=False, force_flag=True)
        assert result["target"] == "special_case"

        # Both parts false
        result = router(closed_by="CUSTOMER", customer_silent=False, force_flag=False)
        assert result["target"] == "normal_case"

    def test_triple_and_chain(self):
        """Test three conditions with &."""
        mock_node = type('MockNode', (), {'full_name': 'data'})()
        a = Ref(mock_node, "a")
        b = Ref(mock_node, "b")
        c = Ref(mock_node, "c")

        router = (if_((a > 0) & (b > 0) & (c > 0), "all_positive")
                  .else_("has_non_positive"))

        result = router(a=1, b=2, c=3)
        assert result["target"] == "all_positive"

        result = router(a=1, b=-1, c=3)
        assert result["target"] == "has_non_positive"

    def test_get_all_vars_extracts_compound_vars(self):
        """Test that BranchNode extracts all vars from compound conditions."""
        mock_node = type('MockNode', (), {'full_name': 'data'})()
        a = Ref(mock_node, "a")
        b = Ref(mock_node, "b")
        c = Ref(mock_node, "c")

        router = (if_((a > 10) & (b == "x") | (c < 5), "target1")
                  .else_("default"))

        # Check that all variables are extracted as inputs
        input_vars = set(router.inputs.keys()) - {"anchor"}
        assert input_vars == {"a", "b", "c"}
