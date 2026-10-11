from pathlib import Path
from unittest import mock

import pytest
import yaml

import libs.Utils.YAML as MUT
import Utils


def test_Render_UsesTemplateEnvironment(monkeypatch: pytest.MonkeyPatch) -> None:
    Utils.AutoPatch(monkeypatch, MUT.Path, 'exists')
    Utils.AutoPatch(monkeypatch, MUT, 'FileSystemLoader')
    Utils.AutoPatch(monkeypatch, MUT, 'Environment')
    MUT.Path.exists.return_value = True
    template = mock.MagicMock(spec=['render'])
    template.render.return_value = 'rendered\n'
    MUT.Environment.return_value.get_template.return_value = template
    path = Path('/templates/vm.yaml')

    result = MUT.YAMLcfg.Render(path, {'name': 'vm-one'})

    assert result == 'rendered\n'
    MUT.FileSystemLoader.assert_called_once_with('/templates')
    MUT.Environment.assert_called_once_with(
        loader=MUT.FileSystemLoader.return_value,
        keep_trailing_newline=True,
    )
    MUT.Environment.return_value.get_template.assert_called_once_with('vm.yaml')
    template.render.assert_called_once_with({'name': 'vm-one'})


def test_Render_RejectsMissingTemplate(monkeypatch: pytest.MonkeyPatch) -> None:
    Utils.AutoPatch(monkeypatch, MUT.Path, 'exists')
    MUT.Path.exists.return_value = False
    path = Path('/missing/template.yaml')

    with pytest.raises(FileNotFoundError, match=str(path)):
        MUT.YAMLcfg.Render(path, {})

    MUT.Path.exists.assert_called_once_with(path)


def test_ToDict_HandlesDictYAMLandBlankInput() -> None:
    original = {'answer': 42}

    assert MUT.YAMLcfg.ToDict(original) is original
    assert MUT.YAMLcfg.ToDict('answer: 42') == original
    assert MUT.YAMLcfg.ToDict('') == {}


def test_ToJSON_HandlesYAMLandDict() -> None:
    assert MUT.YAMLcfg.ToJSON('answer: 42', indent=0) == '{\n"answer": 42\n}'
    assert MUT.YAMLcfg.ToJSON({'answer': 42}) == '{\n  "answer": 42\n}'
    assert MUT.YAMLcfg.ToJSON('') == '{}'


def test_ToYAML_HandlesJSONandDict() -> None:
    expected = 'answer: 42\n'

    assert MUT.YAMLcfg.ToYAML('{"answer": 42}') == expected
    assert MUT.YAMLcfg.ToYAML({'answer': 42}) == expected
    assert yaml.safe_load(expected) == {'answer': 42}
