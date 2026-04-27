# operonx.core.registry

Resource hub, configuration storage, and op registry.

## ResourceHub

::: operonx.core.registry.ResourceHub

## Errors and warnings

::: operonx.core.registry.EnvVarUnsetError
::: operonx.core.registry.ResourceHubWarning

## Bootstrap state

`BOOTSTRAP_ENV_PATHS` is populated by [`operonx.bootstrap`](core.md#operonx.bootstrap)
and surfaces in the `EnvVarUnsetError` message so you can see exactly which
`.env` paths were searched.
