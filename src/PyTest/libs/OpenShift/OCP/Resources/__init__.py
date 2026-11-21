'''OpenShift Resource handling.'''
from dataclasses import dataclass, field
from typing import Any, Optional, Union

import yaml

from libs.CustomTypes import NamedDict
from libs.OpenShift.OCP.CLI.OC import OCcli


@dataclass(frozen=True)
class ResCfg:
    '''
    OpenShift Resource configuration.

    Native mode uses `oc create`.

    Authorative mode uses YAML. This can be used to set fields that are not
    available to be set via native mode or even there is no corresponding `oc
    create` for the resource.

    :param meta:
        Native kind-specific Resource Metadata `NamedDict`. (default: a new
        empty `NamedDict`). Use native names such as `name` or `namespace`,
        not aliases. Supported native `oc create` sub-command fields are
        defined by each resource module. If setting `labels`, `annotations` or
        other fields is desired, use authorative YAML route.
    :param spec:
        Native kind-specific Resource Spec. `NamedDict` (default: a new empty
        `NamedDict`). Field composition is defined by each resource module.
    :param yaml:
        Raw YAML text (default: `None`), describing the Resource Manifest. When
        set to `None`, use native `oc create`. Otherwise it is authoritative,
        i.e. the `meta` and/or `spec` are ignored.
    '''
    meta: NamedDict = field(default_factory=NamedDict)
    spec: NamedDict = field(default_factory=NamedDict)
    yaml: Optional[str] = None


@dataclass(frozen=True)
class OwnInf:
    '''
    Tracking information.

    :param owned:           Ownership (oc, resRef, resUID) record list.
    :param existResDelTO:   Existing resource deletion timeout (default: 5m).
    '''
    owned: list[tuple[OCcli, str, str]]
    existResDelTO: str = '5m'


class SpecMismatch(RuntimeError):
    '''
    Resolved OpenShift Resource Specification differs from desired content.
    '''


def GetUIDs(yamlRes: Union[str, dict[str, Any]]) -> set[str]:
    '''
    Get the UIDs of non-terminating resources.

    Accept a single resource or a resource list (with `.items`), e.g. the
    output of `oc get <resType>{[s]|/<resName>} ... -o {json|yaml}`. A named
    lookup (`<resType>/<resName>`) returns the resource itself. A list or
    selector lookup (`<resType>[s]`) returns `.items`. Each resource is assumed
    to have `.metadata.uid` key.

    Resources being deleted are excluded.

    :param yamlRes:
        A (list of) Resource YAML / JSON text, or its already parsed `dict`.

    :return:    A `set` of the `metadata.uid` of non-terminating resources.

    :raises TypeError:
        The input is not a resource mapping, or its `items` is not a list.
    '''
    res = yaml.safe_load(yamlRes) if isinstance(yamlRes, str) else yamlRes
    if not isinstance(res, dict):
        raise TypeError(
            f'Expected a resource map, got `{type(res).__name__}`.'
        )
    items = res['items'] if ('items' in res) else [res]
    if not isinstance(items, list):
        raise TypeError(
            f'Expected `.items` as a list, got `{type(items).__name__}`.'
        )
    return {
        item['metadata']['uid']
        for item in items
        if ('deletionTimestamp' not in item['metadata'])
    }


def _ValResCfg(
    resCfg: ResCfg,
    vldMeta: tuple[set[str], set[str]],
    vldSpec: tuple[set[str], set[str]],
    expKind: str,
) -> None:
    '''
    Check native key allowlists or authoritative YAML kind, not a full schema.

    Native keys are checked for presence only: a key whose value is `None` or
    empty is present, and values are not validated.

    When the Authoritative YAML is given in `resCfg`, the native `meta` and
    `spec` are ignored.

    :param resCfg:  Resource configuration to check.
    :param vldMeta:
        A (optional, mandatory) `tuple` of key `set`s for native `ResCfg.meta`.
    :param vldSpec:
        A (optional, mandatory) `tuple` of key `set`s for native `ResCfg.spec`.
    :param expKind: Expected YAML Manifest `kind`.

    :raises ValueError: Invalid `resCfg` input.
    '''
    if ((resYAML := resCfg.yaml) is None):
        ((optMeta, mandMeta), (optSpec, mandSpec)) = (vldMeta, vldSpec)
        (metaKeys, specKeys) = (set(resCfg.meta), set(resCfg.spec))
        missMeta = mandMeta - metaKeys
        if (missMeta):
            raise ValueError(
                f'Missing mandatory `resCfg.meta` keys: {missMeta}'
            )
        invldMeta = metaKeys - (optMeta | mandMeta)
        if (invldMeta):
            raise ValueError(
                f'Unsupported `resCfg.meta` keys found: {invldMeta}'
            )
        missSpec = mandSpec - specKeys
        if (missSpec):
            raise ValueError(
                f'Missing mandatory `resCfg.spec` keys: {missSpec}'
            )
        invldSpec = specKeys - (optSpec | mandSpec)
        if (invldSpec):
            raise ValueError(
                f'Unsupported `resCfg.spec` keys found: {invldSpec}'
            )
    else:
        if ((actKind := yaml.safe_load(resYAML)['kind']) != expKind):
            raise ValueError(
                'Invalid `.kind` in Manifest. '
                f'Expected `{expKind}`, got `{actKind}`.'
            )


def _ResCreate(
    oc: OCcli,
    resInput: Union[str, dict[str, Any]],
    ownInf: Optional[OwnInf] = None,
    isSecret: bool = False,
    optsNewRes: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    '''
    Create OpenShift Resource.

    :param oc:
        CLI Client for target OpenShift Cluster.
    :param resInput:
        Resource YAML Manifest or the `OCcli.NewRes()`'s
        `subCmd`/`args`/`opts` input.
    :param ownInf:
        A `OwnInf` instance, containing list to track newly created resource.
        Set to `None` (default) if no tracking is desired.
    :param isSecret:
        Protect command input and output containing credential data.
    :param optsNewRes:
        Resource creation options to be passed to `OCcli.NewRes()`.

    :return:
        Existing or newly (re-)created resource Live Object State in a `dict`.

    :raises RuntimeError:
        Call to `OCcli.NewRes()` failed or `OCcli.EnsureRes()` did not return
        non-empty resource dictionary.
    '''
    optsNewRes = dict(optsNewRes or {})
    optsNewRes.pop('resYAMLorSubCmd', None)
    if (ownInf is None):
        (phase, result, resource) = oc.NewRes(
            resInput,
            **(optsNewRes | {'isSecret': isSecret}),
        )
        if not result.success:
            raise RuntimeError(f'Resource creation failed at {phase.name}.')
    else:
        resource = oc.EnsureRes(
            resInput,
            ownInf.owned,
            isSecret=isSecret,
            existResDelTO=ownInf.existResDelTO,
            optsNewRes=optsNewRes,
        )
    if not (isinstance(resource, dict) and resource):
        raise RuntimeError('No valid resource dictionary was returned.')
    return resource
