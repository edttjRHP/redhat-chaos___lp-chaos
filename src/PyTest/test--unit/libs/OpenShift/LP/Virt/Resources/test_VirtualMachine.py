from unittest import mock

import pytest

import libs.OpenShift.LP.Virt.Resources.VirtualMachine as MUT


@pytest.mark.parametrize('tracked', [False, True])
@pytest.mark.parametrize('options', [
    None, {}, {'waitTime': '15m'}, {'waitCond': None},
    {'waitCond': 'condition=Ready', 'waitTime': '7m'},
])
def test_Create_ForwardsYAMLAndOptions(
    tracked: bool,
    options: dict | None,
) -> None:
    oc = mock.sentinel.oc
    resource = {'kind': 'VirtualMachine'}
    manifest = 'kind: VirtualMachine\nmetadata:\n  name: example\n'
    resCfg = mock.Mock(yaml=manifest)
    ownInf = mock.sentinel.ownInf if tracked else None
    original = dict(options) if (options is not None) else None

    with (
        mock.patch.object(MUT, '_ValResCfg') as valResCfg,
        mock.patch.object(
            MUT, '_ResCreate', return_value=resource,
        ) as resCreate,
    ):
        result = MUT.Create(oc, resCfg, ownInf, options)

    assert (result is resource)
    assert (options == original)
    valResCfg.assert_called_once_with(resCfg, set(), set(), 'VirtualMachine')
    resCreate.assert_called_once_with(oc, manifest, ownInf, optsNewRes=options)
    assert (resCreate.call_args.kwargs['optsNewRes'] is options)


def test_Create_RejectsMissingYAMLBeforeValidation() -> None:
    resCfg = mock.Mock(yaml=None)
    with (
        mock.patch.object(MUT, '_ValResCfg') as valResCfg,
        mock.patch.object(MUT, '_ResCreate') as resCreate,
    ):
        with pytest.raises(ValueError) as caught:
            MUT.Create(mock.sentinel.oc, resCfg)

    assert (str(caught.value) == 'VirtualMachine creation requires YAML.')
    valResCfg.assert_not_called()
    resCreate.assert_not_called()


def test_Create_PropagatesValidationFailure() -> None:
    resCfg = mock.Mock(yaml='kind: Secret')
    validationError = ValueError('invalid kind')
    with (
        mock.patch.object(
            MUT, '_ValResCfg', side_effect=validationError,
        ) as valResCfg,
        mock.patch.object(MUT, '_ResCreate') as resCreate,
    ):
        with pytest.raises(ValueError) as caught:
            MUT.Create(mock.sentinel.oc, resCfg)

    assert (caught.value is validationError)
    valResCfg.assert_called_once_with(resCfg, set(), set(), 'VirtualMachine')
    resCreate.assert_not_called()


def test_Create_PropagatesCreationFailure() -> None:
    oc = mock.sentinel.oc
    resCfg = mock.Mock(yaml='kind: VirtualMachine')
    ownInf = mock.sentinel.ownInf
    options = {'waitTime': '15m'}
    creationError = RuntimeError('creation failed')
    with (
        mock.patch.object(MUT, '_ValResCfg') as valResCfg,
        mock.patch.object(
            MUT, '_ResCreate', side_effect=creationError,
        ) as resCreate,
    ):
        with pytest.raises(RuntimeError) as caught:
            MUT.Create(oc, resCfg, ownInf, options)

    assert (caught.value is creationError)
    valResCfg.assert_called_once_with(resCfg, set(), set(), 'VirtualMachine')
    resCreate.assert_called_once_with(
        oc, resCfg.yaml, ownInf, optsNewRes=options,
    )
