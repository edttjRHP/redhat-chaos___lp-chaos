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


def _ValResCfg(
    resCfg: ResCfg, vldMeta: set, vldSpec: set, expKind: str,
) -> None:
    '''
    Check native key allowlists or authoritative YAML kind, not a full schema.

    :param resCfg:      Resource configuration to check.
    :param vldMeta:     A `set` of valid keys in `ResCfg.meta`.
    :param vldSpec:     A `set` of valid keys in `ResCfg.spec`.
    :param expKind:     Expected YAML Manifest `kind`.

    :raises ValueError: Invalid `resCfg` input.
    '''
    if ((resYAML := resCfg.yaml) is None):
        invMeta = set(resCfg.meta) - vldMeta
        if (invMeta):
            raise ValueError(
                f'Unsupported `resCfg.meta` keys found: {invMeta}'
            )
        invSpec = set(resCfg.spec) - vldSpec
        if (invSpec):
            raise ValueError(
                f'Unsupported `resCfg.meta` keys found: {invSpec}'
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

    :return:
        Existing or newly (re-)created resource Live Object State in a `dict`.

    :raises RuntimeError:
        Call to `OCcli.NewRes()` failed or `OCcli.EnsureRes()` did not return
        non-empty resource dictionary.
    '''
    if (ownInf is None):
        (phase, result, resource) = oc.NewRes(resInput, isSecret=isSecret)
        if not result.success:
            raise RuntimeError(f'Resource creation failed at {phase.name}.')
    else:
        resource = oc.EnsureRes(
            resInput,
            ownInf.owned,
            isSecret=isSecret,
            existResDelTO=ownInf.existResDelTO,
        )
    if not (isinstance(resource, dict) and resource):
        raise RuntimeError('No valid resource dictionary was returned.')
    return resource
