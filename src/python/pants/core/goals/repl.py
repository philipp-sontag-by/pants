# Copyright 2020 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).
import os
from abc import ABC
from dataclasses import dataclass
from pathlib import PurePath
from typing import ClassVar, Dict, Mapping, Optional, Tuple, Type, cast

from pants.base.build_root import BuildRoot
from pants.engine.console import Console
from pants.engine.fs import Digest, Workspace
from pants.engine.goal import Goal, GoalSubsystem
from pants.engine.process import InteractiveProcess, InteractiveRunner
from pants.engine.rules import Get, collect_rules, goal_rule
from pants.engine.target import Targets, TransitiveTargets
from pants.engine.unions import UnionMembership, union
from pants.option.global_options import GlobalOptions
from pants.util.contextutil import temporary_dir
from pants.util.frozendict import FrozenDict
from pants.util.meta import frozen_after_init


@union
@dataclass(frozen=True)
class ReplImplementation(ABC):
    """A REPL implementation for a specific language or runtime.

    Proxies from the top-level `repl` goal to an actual implementation.
    """

    name: ClassVar[str]
    targets: Targets
    chroot: str  # Absolute path of the chroot the sources will be materialized to.

    def in_chroot(self, relpath: str) -> str:
        return os.path.join(self.chroot, relpath)


class ReplSubsystem(GoalSubsystem):
    """Opens a REPL."""

    name = "repl"
    required_union_implementations = (ReplImplementation,)

    @classmethod
    def register_options(cls, register) -> None:
        super().register_options(register)
        register(
            "--shell",
            type=str,
            default=None,
            help="Override the automatically-detected REPL program for the target(s) specified. ",
        )

    @property
    def shell(self) -> Optional[str]:
        return cast(Optional[str], self.options.shell)


class Repl(Goal):
    subsystem_cls = ReplSubsystem


@frozen_after_init
@dataclass(unsafe_hash=True)
class ReplRequest:
    digest: Digest
    args: Tuple[str, ...]
    env: FrozenDict[str, str]

    def __init__(
        self, *, digest: Digest, args: Tuple[str, ...], env: Optional[Mapping[str, str]] = None,
    ) -> None:
        self.digest = digest
        self.args = args
        self.env = FrozenDict(env or {})


@goal_rule
async def run_repl(
    console: Console,
    workspace: Workspace,
    interactive_runner: InteractiveRunner,
    repl_subsystem: ReplSubsystem,
    transitive_targets: TransitiveTargets,
    build_root: BuildRoot,
    union_membership: UnionMembership,
    global_options: GlobalOptions,
) -> Repl:
    # TODO: When we support multiple languages, detect the default repl to use based
    #  on the targets.  For now we default to the python repl.
    repl_shell_name = repl_subsystem.shell or "python"

    implementations: Dict[str, Type[ReplImplementation]] = {
        impl.name: impl for impl in union_membership[ReplImplementation]
    }
    repl_implementation_cls = implementations.get(repl_shell_name)
    if repl_implementation_cls is None:
        available = sorted(implementations.keys())
        console.print_stderr(
            f"{repr(repl_shell_name)} is not a registered REPL. Available REPLs (which may "
            f"be specified through the option `--repl-shell`): {available}"
        )
        return Repl(-1)

    with temporary_dir(root_dir=global_options.options.pants_workdir, cleanup=False) as tmpdir:
        repl_impl = repl_implementation_cls(
            targets=Targets(transitive_targets.closure), chroot=tmpdir
        )
        request = await Get(ReplRequest, ReplImplementation, repl_impl)

        workspace.write_digest(
            request.digest, path_prefix=PurePath(tmpdir).relative_to(build_root.path).as_posix()
        )
        result = interactive_runner.run(
            InteractiveProcess(argv=request.args, env=request.env, run_in_workspace=True)
        )
    return Repl(result.exit_code)


def rules():
    return collect_rules()
