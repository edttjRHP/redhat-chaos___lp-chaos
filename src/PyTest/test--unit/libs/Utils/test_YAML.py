'''Isolated tests for YAML configuration rendering and format conversion.'''
from unittest import mock

import pytest
from yaml import YAMLError

import libs.Utils.YAML as MUT


MAPPING_CASES = [
    ({}, {}),
    ('{}', {}),
    ('answer: 42', {'answer': 42}),
    ('{answer: 42}', {'answer': 42}),
    ('{"answer": 42}', {'answer': 42}),
    ({'value': None, 'items': [None, False, 0, '', [], {}]}, {
        'value': None, 'items': [None, False, 0, '', [], {}],
    }),
    ('value: null\nitems: [null, false, 0, "", [], {}]', {
        'value': None, 'items': [None, False, 0, '', [], {}],
    }),
    ('{"value": null, "items": [null, false, 0, "", [], {}]}', {
        'value': None, 'items': [None, False, 0, '', [], {}],
    }),
]
NON_MAPPING_CASES = [
    (None, None), (False, None), (True, None), (0, None), (1, None),
    (0.0, None), ([], None), ([1], None), ((), None), (set(), None),
    (b'{}', None), ('', None), (' \n', None), ('# comment\n', None),
    ('null', None), ('Null', None), ('NULL', None), ('~', None),
    ('false', False), ('true', True), ('0', 0), ('1', 1), ('0.0', 0.0),
    ('[]', []), ('[1]', [1]), ('- item', ['item']), ('""', ''),
    ('"null"', 'null'), ('text', 'text'),
]


@pytest.fixture(autouse=True, name='collaborators')
def PatchCollaborators(monkeypatch: pytest.MonkeyPatch) -> None:
    '''Replace parser, serializer and renderer collaborators.'''
    patches = {
        name: mock.Mock(name=name)
        for name in ('yaml', 'json', 'Environment', 'FileSystemLoader')
    }
    for (name, replacement) in patches.items():
        monkeypatch.setattr(MUT, name, replacement)


@pytest.fixture(name='templatePath')
def TemplatePath() -> mock.Mock:
    '''Return a template-path double without accessing the filesystem.'''
    path = mock.MagicMock(spec=MUT.Path)
    path.__str__.return_value = '/templates/vm.yaml'
    path.exists.return_value = True
    path.parent = '/templates'
    path.name = 'vm.yaml'
    return path


def test_Render_UsesTemplateEnvironment(templatePath: mock.Mock) -> None:
    context = {'name': 'vm-one'}
    environment = MUT.Environment.return_value
    template = environment.get_template.return_value
    template.render.return_value = 'rendered\n'

    result = MUT.YAMLcfg.Render(templatePath, context)

    assert (result == 'rendered\n')
    templatePath.exists.assert_called_once_with()
    MUT.FileSystemLoader.assert_called_once_with('/templates')
    MUT.Environment.assert_called_once_with(
        loader=MUT.FileSystemLoader.return_value,
        keep_trailing_newline=True,
    )
    environment.get_template.assert_called_once_with('vm.yaml')
    template.render.assert_called_once_with(context)
    assert (template.render.call_args.args[0] is context)


def test_Render_RejectsMissingTemplate(templatePath: mock.Mock) -> None:
    templatePath.exists.return_value = False

    message = '^Template file not found at: /templates/vm[.]yaml$'
    with pytest.raises(FileNotFoundError, match=message):
        MUT.YAMLcfg.Render(templatePath, {})

    templatePath.exists.assert_called_once_with()
    MUT.FileSystemLoader.assert_not_called()
    MUT.Environment.assert_not_called()


@pytest.mark.parametrize('stage', [
    'exists', 'loader', 'environment', 'get_template', 'render',
])
def test_Render_PropagatesFailures(
    templatePath: mock.Mock,
    stage: str,
) -> None:
    environment = MUT.Environment.return_value
    template = environment.get_template.return_value
    target = {
        'exists': templatePath.exists,
        'loader': MUT.FileSystemLoader,
        'environment': MUT.Environment,
        'get_template': environment.get_template,
        'render': template.render,
    }[stage]
    error = RuntimeError('render collaborator failed')
    target.side_effect = error

    with pytest.raises(RuntimeError) as raised:
        MUT.YAMLcfg.Render(templatePath, {})

    assert (raised.value is error)
    target.assert_called_once()
    if (stage == 'exists'): MUT.FileSystemLoader.assert_not_called()
    if (stage in ('exists', 'loader')): MUT.Environment.assert_not_called()
    if (stage in ('exists', 'loader', 'environment')):
        environment.get_template.assert_not_called()
    if (stage != 'render'): template.render.assert_not_called()


@pytest.mark.parametrize('method', ['ToDict', 'ToJSON', 'ToYAML'])
@pytest.mark.parametrize(('data', 'expected'), MAPPING_CASES)
def test_Conversion_PreservesMappingsAndNestedValues(
    method: str,
    data: object,
    expected: dict,
) -> None:
    MUT.yaml.safe_load.return_value = expected
    MUT.json.dumps.return_value = 'serialized-json'
    MUT.yaml.safe_dump.return_value = 'serialized-yaml'

    actual = getattr(MUT.YAMLcfg, method)(data)

    mapping = data if (isinstance(data, dict)) else expected
    if (method == 'ToDict'):
        assert (actual is mapping)
        assert (actual == expected)
        MUT.json.dumps.assert_not_called()
        MUT.yaml.safe_dump.assert_not_called()
    elif (method == 'ToJSON'):
        assert (actual == 'serialized-json')
        MUT.json.dumps.assert_called_once_with(mapping, indent=2)
        assert (MUT.json.dumps.call_args.args[0] is mapping)
        MUT.yaml.safe_dump.assert_not_called()
    else:
        assert (actual == 'serialized-yaml')
        MUT.yaml.safe_dump.assert_called_once_with(
            mapping, default_flow_style=False, sort_keys=False,
        )
        assert (MUT.yaml.safe_dump.call_args.args[0] is mapping)
        MUT.json.dumps.assert_not_called()
    if (isinstance(data, str)):
        MUT.yaml.safe_load.assert_called_once_with(data)
    else: MUT.yaml.safe_load.assert_not_called()


@pytest.mark.parametrize('method', ['ToDict', 'ToJSON', 'ToYAML'])
@pytest.mark.parametrize(('data', 'parsed'), NON_MAPPING_CASES)
def test_Conversion_RejectsNonMappingInputs(
    method: str,
    data: object,
    parsed: object,
) -> None:
    MUT.yaml.safe_load.return_value = parsed

    with pytest.raises(TypeError, match='Expected a dict or YAML/JSON text'):
        getattr(MUT.YAMLcfg, method)(data)

    if (isinstance(data, str)):
        MUT.yaml.safe_load.assert_called_once_with(data)
    else: MUT.yaml.safe_load.assert_not_called()
    MUT.json.dumps.assert_not_called()
    MUT.yaml.safe_dump.assert_not_called()


@pytest.mark.parametrize('method', ['ToDict', 'ToJSON', 'ToYAML'])
@pytest.mark.parametrize('data', ['{broken', 'value: [1, 2'])
def test_Conversion_PropagatesMalformedInput(method: str, data: str) -> None:
    error = YAMLError('parser rejected malformed input')
    MUT.yaml.safe_load.side_effect = error

    with pytest.raises(YAMLError) as raised:
        getattr(MUT.YAMLcfg, method)(data)

    assert (raised.value is error)
    MUT.yaml.safe_load.assert_called_once_with(data)
    MUT.json.dumps.assert_not_called()
    MUT.yaml.safe_dump.assert_not_called()


@pytest.mark.parametrize('indent', [0, 2])
@pytest.mark.parametrize('data', ['answer: 42', {'answer': 42}])
def test_ToJSON_ForwardsIndentAndSerializerResult(
    data: object,
    indent: int,
) -> None:
    mapping = {'answer': 42}
    MUT.yaml.safe_load.return_value = mapping
    MUT.json.dumps.return_value = 'json-output\n'

    assert (MUT.YAMLcfg.ToJSON(data, indent=indent) == 'json-output\n')

    MUT.json.dumps.assert_called_once_with(mapping, indent=indent)


@pytest.mark.parametrize('data', [
    'answer: 42', '{"answer": 42}', {'answer': 42},
])
def test_ToYAML_ForwardsSerializerResult(data: object) -> None:
    mapping = {'answer': 42}
    MUT.yaml.safe_load.return_value = mapping
    MUT.yaml.safe_dump.return_value = 'yaml-output\n'

    assert (MUT.YAMLcfg.ToYAML(data) == 'yaml-output\n')

    MUT.yaml.safe_dump.assert_called_once_with(
        mapping, default_flow_style=False, sort_keys=False,
    )


@pytest.mark.parametrize(('data', 'parsed'), [
    ('false', False), ('0', 0), ('0.0', 0.0), ('[]', []), ('""', ''),
    ('', None), (' \n', None), ('# comment\n', None), ('null', None),
    ('~', None),
])
def test_ToJSON_RejectsFalsyAndNullRoots(data: str, parsed: object) -> None:
    MUT.yaml.safe_load.return_value = parsed

    with pytest.raises(TypeError): MUT.YAMLcfg.ToJSON(data)

    MUT.yaml.safe_load.assert_called_once_with(data)
    MUT.json.dumps.assert_not_called()


@pytest.mark.parametrize('method', ['ToJSON', 'ToYAML'])
def test_Conversion_PropagatesSerializerFailures(method: str) -> None:
    mapping = {'answer': 42}
    error = TypeError('serializer failed')
    serializer = MUT.json.dumps if (method == 'ToJSON') else MUT.yaml.safe_dump
    serializer.side_effect = error

    with pytest.raises(TypeError) as raised:
        getattr(MUT.YAMLcfg, method)(mapping)

    assert (raised.value is error)
    assert (serializer.call_args.args[0] is mapping)
    MUT.yaml.safe_load.assert_not_called()
