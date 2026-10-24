# PyTest Agent Guidance
This guidance supplements the repository-root `.AI_INIT.md` and
`.AI_README.md` for `src/PyTest/`. Root guidance takes precedence if any rule
conflicts.

## Code Style and Architecture
- Target Python 3.11, matching the repository setup. Use type hints for all
  function signatures.
- Follow the team Best Practices naming and quoting conventions. Apply PEP 8
  where it does not conflict with those conventions.
- Prefer explicit imports over wildcard imports.

## Testing Guidelines
- Put isolated unit tests under `test--unit/`, matching the source layout, and
  scenario tests under `test--suites/`; do not create a `tests/` directory.
- In unit tests, import the module under test as `MUT` and import shared helpers
  from `test--unit/Utils.py` as `Utils`.
- Derive test names from the method or function under test, followed by the
  expected behavior. Use its exact name for ordinary methods and functions,
  such as `test_Run_SuccessPassesArguments` or
  `test__Status_ReturnsValueOnlyForSuccess`. For special, dunder, and
  pseudo-dunder methods or functions, use readable paraphrases instead of
  literal underscore-heavy names, such as `test_Init_DelegatesToBase`,
  `test_CheckKeys_RejectsInvalidIdentifiers`, or
  `test_OperatorOr_DeepMergePreservesExactTypesAndInputs`. Tests spanning
  multiple special, dunder, or pseudo-dunder subjects may use a readable
  aggregate name; ordinary methods and functions retain their exact names.
  Constant tests name the constant rather than a function.
- Prefer pytest fixtures over setup and teardown methods. Put shared fixtures
  in the applicable `conftest.py`.
- Run isolated tests with `pytest--unit.ini`. From `src/PyTest/`, run scenarios
  with the explicit path: `python -m pytest -c pytest.ini test--suites/`.
