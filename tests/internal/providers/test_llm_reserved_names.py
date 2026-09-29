"""Mistakes in ``LLMOp.of(...)`` that are knowable when the op is built.

Both used to surface one model call later, or never:

* ``prompt={"user": "{user}"}, user=q`` — ``user`` is a model setting
  (OpenAI's end-user id), so ``user=q`` went to the provider and the
  ``{user}`` placeholder had nothing to fill it. The op failed at run
  time with "Missing template variable(s)", and ``run()`` returned ``{}``.
* ``validators=PARENT["allowed"]`` — validators are build-time values and
  a Ref there is never resolved.
"""

from __future__ import annotations

import pytest

from operonx.core.exceptions import PromptError
from operonx.providers.ops import LLMOp
from operonx.providers.ops.llm import RESERVED_KEYS

pytestmark = pytest.mark.unit


class TestPlaceholderNamedLikeAModelSetting:
    def test_user_placeholder_with_user_kwarg_fails_at_construction(self):
        with pytest.raises(PromptError, match=r"\{user\}.*model setting"):
            LLMOp.of(resource="r", prompt={"system": "Be brief.", "user": "{user}"}, user="hi")

    def test_the_error_suggests_a_rename(self):
        with pytest.raises(PromptError, match="user_prompt"):
            LLMOp.of(resource="r", prompt={"user": "{user}"}, user="hi")

    @pytest.mark.parametrize("name", sorted(RESERVED_KEYS - {"prompt", "messages"}))
    def test_every_setting_name_is_caught_even_without_its_kwarg(self, name):
        """``{temperature}`` fails the same way whether or not
        ``temperature=`` was passed: the setting always has a default, so
        the key never reaches the template."""
        with pytest.raises(PromptError, match=rf"\{{{name}\}}"):
            LLMOp.of(resource="r", prompt=f"Say {{{name}}}")

    def test_a_ref_template_is_explained_at_run_time(self):
        """A template that arrives through a Ref is only known at run time;
        the error then says why the variable is missing."""
        op = LLMOp.of(resource="r", prompt=None)
        with pytest.raises(PromptError, match=r"model setting"):
            op._build_llm_params({"prompt": "Q: {user}", "user": "u-1"})


class TestGenuineSettingsStillWork:
    def test_settings_next_to_template_variables(self):
        op = LLMOp.of(resource="r", prompt="Hi {name}", name="Ann", temperature=0.3, user="u-1")
        params = op._build_llm_params(
            {"prompt": "Hi {name}", "name": "Ann", "temperature": 0.3, "user": "u-1"}
        )
        assert params["messages"] == [{"role": "user", "content": "Hi Ann"}]
        assert params["temperature"] == 0.3
        assert params["user"] == "u-1"

    def test_user_with_messages(self):
        op = LLMOp.of(resource="r", messages=[{"role": "user", "content": "{not a template}"}])
        params = op._build_llm_params(
            {"messages": [{"role": "user", "content": "{not a template}"}], "user": "u-1"}
        )
        assert params["user"] == "u-1"
        assert params["messages"][0]["content"] == "{not a template}"

    def test_a_prompt_whose_text_mentions_a_setting_without_braces(self):
        LLMOp.of(resource="r", prompt={"system": "Set the temperature.", "user": "{q}"}, q="x")
