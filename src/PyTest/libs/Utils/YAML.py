'''
YAML configuration utilities: Jinja2 template rendering and format conversion.
'''
import json
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader
import yaml


class YAMLcfg:
    '''
    YAML configuration helper for Jinja2 template rendering and format
    conversions (YAML, JSON, and Python dict). Each conversion accepts a
    `dict` or YAML/JSON text representing a top-level mapping.
    '''

    @staticmethod
    def Render(tmplPath: Path, ctxData: dict[str, Any]) -> str:
        '''
        Render a Jinja2 template file to a YAML string.

        :param tmplPath:            The `Path` to the Jinja2 template file.
        :param ctxData:             Context data `dict` for template rendering.

        :return:                    Rendered YAML as a `str`.

        :raises FileNotFoundError:  If the template file does not exist.
        '''
        if not tmplPath.exists():
            raise FileNotFoundError(
                f'Template file not found at: {tmplPath}'
            )
        return Environment(
            loader=FileSystemLoader(str(tmplPath.parent)),
            keep_trailing_newline=True,
        ).get_template(tmplPath.name).render(ctxData)

    @staticmethod
    def ToDict(data: 'str | dict[str, Any]') -> dict[str, Any]:
        '''
        Parse YAML/JSON mapping input or return a supplied `dict`.

        :param data:    YAML/JSON `str` or `dict` (returned as-is).

        :return:    A `dict`.

        :raises TypeError:  If the input or parsed value is not a `dict`.
        '''
        if isinstance(data, str): data = yaml.safe_load(data)
        if not isinstance(data, dict):
            raise TypeError(
                'Expected a dict or YAML/JSON text representing '
                'a top-level mapping.'
            )
        return data

    @staticmethod
    def ToJSON(data: 'str | dict[str, Any]', indent: int = 2) -> str:
        '''
        Serialize as JSON.

        :param data:    YAML/JSON `str` or `dict`.
        :param indent:  JSON indentation level (default: `2`).

        :return:        JSON `str`, preserving nested nulls.
        '''
        return json.dumps(YAMLcfg.ToDict(data), indent=indent)

    @staticmethod
    def ToYAML(data: 'str | dict[str, Any]') -> str:
        '''
        Serialize as YAML.

        :param data:    YAML/JSON `str` or `dict`.

        :return:        YAML `str`, preserving nested nulls.
        '''
        return yaml.safe_dump(
            YAMLcfg.ToDict(data),
            default_flow_style=False,
            sort_keys=False,
        )
