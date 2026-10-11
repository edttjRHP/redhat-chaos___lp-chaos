'''
YAML configuration utilities: Jinja2 template rendering and format conversion.
'''
import json
from pathlib import Path
from typing import Any, Dict

from jinja2 import Environment, FileSystemLoader
import yaml


class YAMLcfg:
    '''
    YAML configuration helper for Jinja2 template rendering and format
    conversions (YAML, JSON, and Python dict).
    '''

    @staticmethod
    def Render(templPath: Path, ctxData: Dict[str, Any]) -> str:
        '''
        Render a Jinja2 template file to a YAML string.

        :param templPath:           The `Path` to the Jinja2 template file.
        :param ctxData:             Context data `dict` for template rendering.
        :return:                    Rendered YAML as a `str`.
        :raises FileNotFoundError:  If the template file does not exist.
        '''
        if not templPath.exists():
            raise FileNotFoundError(
                f'Template file not found at: {templPath}'
            )
        return Environment(
            loader=FileSystemLoader(str(templPath.parent)),
            keep_trailing_newline=True,
        ).get_template(templPath.name).render(ctxData)

    @staticmethod
    def ToDict(data: 'str | Dict[str, Any]') -> Dict[str, Any]:
        '''
        Parse to a `dict`.

        :param data:    YAML/JSON `str` or `dict` (returned as-is).
        :return:        Parsed `dict` (empty `dict` if the input is blank).
        '''
        if isinstance(data, dict): return data
        return (yaml.safe_load(data) or {})

    @staticmethod
    def ToJSON(data: 'str | Dict[str, Any]', indent: int = 2) -> str:
        '''
        Serialize as JSON.

        :param data:    YAML/JSON `str` or `dict`.
        :param indent:  JSON indentation level (default: `2`).
        :return:        JSON `str`.
        '''
        if isinstance(data, str): data = yaml.safe_load(data) or {}
        return json.dumps(data, indent=indent)

    @staticmethod
    def ToYAML(data: 'str | Dict[str, Any]') -> str:
        '''
        Serialize as YAML.

        :param data:    JSON `str` or `dict`.
        :return:        YAML `str`.
        '''
        if isinstance(data, str): data = json.loads(data)
        return yaml.safe_dump(data, default_flow_style=False, sort_keys=False)
