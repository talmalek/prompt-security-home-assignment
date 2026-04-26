"""Soft assertions with Allure step integration (non-blocking checks until assert_all)."""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

import allure
import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator


class SoftAssert:
    """Collect assertion failures and report them together; integrates with allure.step."""

    def __init__(self) -> None:
        self.errors: list[str] = []

    @contextmanager
    def step(self, step_name: str) -> Iterator[None]:
        """Allure step with soft-assert error tracking."""
        errors_before = len(self.errors)
        with allure.step(step_name):
            try:
                yield
            except Exception as e:
                self.fail(f"{step_name} failed: {e!s}")

            if len(self.errors) > errors_before:
                step_errors = self.errors[errors_before:]
                # Clear the errors we are about to raise so that a subsequent
                # call to assert_all() in teardown_method does not double-report
                # the same failures as a teardown ERROR.
                self.errors = self.errors[:errors_before]
                raise AssertionError("; ".join(step_errors))

    def check(self, condition: bool, msg: str) -> None:
        if not condition:
            self.errors.append(msg)

    def check_equal(self, *, actual: object, expected: object, message: str) -> None:
        if actual != expected:
            self.errors.append(f"{message}: {actual!r} != {expected!r}")

    def check_not_equal(self, *, a: object, b: object, msg: str) -> None:
        if a == b:
            self.errors.append(f"{msg}: {a!r} == {b!r}")

    def check_in(self, *, item: object, container: object, msg: str) -> None:
        if item not in container:  # type: operator contains
            self.errors.append(f"{msg}: {item!r} not in {container!r}")

    def check_not_in(self, *, item: object, container: object, msg: str) -> None:
        if item in container:  # type: operator contains
            self.errors.append(f"{msg}: {item!r} in {container!r}")

    def check_true(self, condition: bool, msg: str) -> None:
        if not condition:
            self.errors.append(msg)

    def check_false(self, condition: bool, msg: str) -> None:
        if condition:
            self.errors.append(msg)

    def fail(self, msg: str) -> None:
        self.errors.append(msg)

    def assert_all(self) -> None:
        if self.errors:
            msg = "Soft assert failures:\n" + "\n".join(self.errors)
            pytest.fail(msg, pytrace=False)
