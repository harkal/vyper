import functools
from typing import Any, Optional

from vyper import ast as vy_ast
from vyper.ast.validation import validate_call_args
from vyper.codegen.expr import Expr
from vyper.codegen.ir_node import IRnode
from vyper.exceptions import CompilerPanic, TypeMismatch, UnfoldableNode
from vyper.semantics.analysis.base import Modifiability, StateMutability
from vyper.semantics.analysis.utils import (
    check_modifiability,
    get_exact_type_from_node,
    validate_expected_type,
)
from vyper.semantics.types import TYPE_T, KwargSettings, VyperType
from vyper.semantics.types.utils import type_from_annotation


def process_arg(arg, expected_arg_type, context):
    # If the input value is a typestring, return the equivalent codegen type for IR generation
    if isinstance(expected_arg_type, TYPE_T):
        return expected_arg_type.typedef

    # if it is a word type, return a stack item.
    # TODO: remove this case, builtins should not require value expressions
    if expected_arg_type._is_prim_word:
        return Expr.parse_value_expr(arg, context)

    if isinstance(expected_arg_type, VyperType):
        return Expr(arg, context).ir_node

    raise CompilerPanic(f"Unexpected type: {expected_arg_type}")  # pragma: nocover


def process_kwarg(kwarg_node, kwarg_settings, expected_kwarg_type, context):
    if kwarg_settings.require_literal:
        return kwarg_node.get_folded_value().value

    return process_arg(kwarg_node, expected_kwarg_type, context)


def process_inputs(wrapped_fn):
    """
    Generate IR for input arguments on builtin functions.

    Applied as a wrapper on the `build_IR` method of
    classes in `vyper.functions.functions`.
    """

    @functools.wraps(wrapped_fn)
    def decorator_fn(self, node, context):
        subs = []
        for arg in node.args:
            arg_ir = process_arg(arg, arg._metadata["type"], context)
            # TODO annotate arg_ir with argname from self._inputs?
            subs.append(arg_ir)

        kwsubs = {}

        # note: must compile in source code order, left-to-right
        expected_kwarg_types = self.infer_kwarg_types(node)

        for k in node.keywords:
            kwarg_settings = self._kwargs[k.arg]
            expected_kwarg_type = expected_kwarg_types[k.arg]
            kwsubs[k.arg] = process_kwarg(k.value, kwarg_settings, expected_kwarg_type, context)

        # add kwargs which were not specified in the source
        for k, expected_arg in self._kwargs.items():
            if k not in kwsubs:
                kwsubs[k] = expected_arg.default

        for k, v in kwsubs.items():
            if isinstance(v, IRnode):
                v.annotation = k

        return wrapped_fn(self, node, subs, kwsubs, context)

    return decorator_fn


class BuiltinFunctionT(VyperType):
    typeclass = "builtin_function"

    _has_varargs = False
    _inputs: list[tuple[str, Any]] = []
    _kwargs: dict[str, KwargSettings] = {}
    _modifiability: Modifiability = Modifiability.MODIFIABLE
    _return_type: Optional[VyperType] = None
    _equality_attrs = ("_id",)
    _is_terminus = False
    mutability: StateMutability = StateMutability.PURE

    @property
    def modifiability(self):
        return self._modifiability

    # helper function to deal with TYPE_Ts
    def _validate_single(self, arg: vy_ast.VyperNode, expected_type: VyperType) -> None:
        if TYPE_T.any().compare_type(expected_type):
            # try to parse the type - call type_from_annotation
            # for its side effects (will throw if is not a type)
            type_from_annotation(arg)
        else:
            validate_expected_type(arg, expected_type)

    def _validate_arg_types(self, node: vy_ast.Call) -> None:
        num_args = len(self._inputs)  # the number of args the signature indicates

        expect_num_args: Any = num_args
        if self._has_varargs:
            # note special meaning for -1 in validate_call_args API
            expect_num_args = (num_args, -1)

        validate_call_args(node, expect_num_args, list(self._kwargs.keys()))

        for arg, (_, expected) in zip(node.args, self._inputs):
            self._validate_single(arg, expected)

        for kwarg in node.keywords:
            kwarg_settings = self._kwargs[kwarg.arg]
            if kwarg_settings.require_literal and not check_modifiability(
                kwarg.value, Modifiability.CONSTANT
            ):
                raise TypeMismatch("Value must be literal", kwarg.value)
            self._validate_single(kwarg.value, kwarg_settings.typ)

        # typecheck varargs. we don't have type info from the signature,
        # so ensure that the types of the args can be inferred exactly.
        varargs = node.args[num_args:]
        if len(varargs) > 0:
            assert self._has_varargs  # double check validate_call_args
        for arg in varargs:
            # call get_exact_type_from_node for its side effects -
            # ensures the type can be inferred exactly.
            get_exact_type_from_node(arg)

    def check_modifiability_for_call(self, node: vy_ast.Call, modifiability: Modifiability) -> bool:
        return self._modifiability <= modifiability

    def fetch_call_return(self, node: vy_ast.Call) -> Optional[VyperType]:
        self._validate_arg_types(node)

        return self._return_type

    def infer_arg_types(self, node: vy_ast.Call, expected_return_typ=None) -> list[VyperType]:
        self._validate_arg_types(node)
        ret = [expected for (_, expected) in self._inputs]

        # handle varargs.
        n_known_args = len(self._inputs)
        varargs = node.args[n_known_args:]
        if len(varargs) > 0:
            assert self._has_varargs
        ret.extend(get_exact_type_from_node(arg) for arg in varargs)
        return ret

    def infer_kwarg_types(self, node: vy_ast.Call) -> dict[str, VyperType]:
        return {i.arg: self._kwargs[i.arg].typ for i in node.keywords}

    def __repr__(self):
        return f"(builtin) {self._id}"

    def _try_fold(self, node):
        raise UnfoldableNode(f"not foldable: {self}", node)
