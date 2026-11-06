"""Unit tests for the remediation that supplies inputs a run left out."""

from __future__ import annotations

from dovo.common.models import Remediation
from dovo.core.inputs import (
    InputType,
    ParameterInput,
    missing_inputs_remediation,
)


class MissingInputsRemediationTests:
    """Unit tests verifying the command that runs the blueprint again with every missing input."""

    def test_passes_each_missing_input_by_alias_or_generic_flag(self) -> None:
        declarations = {
            "message": ParameterInput(type=InputType.STRING, required=True, aliases=["-m", "--message"]),
            "target_env": ParameterInput(type=InputType.STRING, required=True),
        }

        remediation = missing_inputs_remediation(
            name="deploy", missing=["message", "target_env"], declarations=declarations
        )

        command = "dovo run deploy -m <message> -i target_env=<target-env>"
        assert remediation == Remediation(message=f"Run '{command}'", command=command)

    def test_uses_long_input_flag_when_an_input_takes_short_flag_as_alias(self) -> None:
        declarations = {
            "issue": ParameterInput(type=InputType.STRING, required=True, aliases=["-i"]),
            "base": ParameterInput(type=InputType.STRING, required=True),
        }

        remediation = missing_inputs_remediation(name="ship", missing=["base"], declarations=declarations)

        assert remediation.command == "dovo run ship --input base=<base>"
