import ast as python_ast
from typing import Any, Optional, Sequence, Type, Union

from .natspec import parse_natspec as parse_natspec
from .parse import parse_to_ast as parse_to_ast
from .parse import parse_to_ast_with_settings as parse_to_ast_with_settings
from .utils import ast_to_dict as ast_to_dict

NODE_BASE_ATTRIBUTES: Any
NODE_SRC_ATTRIBUTES: Any
DICT_AST_SKIPLIST: Any

def get_node(
    ast_struct: Union[dict, python_ast.AST], parent: Optional[VyperNode] = ...
) -> VyperNode: ...
def compare_nodes(left_node: VyperNode, right_node: VyperNode) -> bool: ...

class VyperNode:
    full_source_code: str = ...
    node_source_code: str = ...
    _metadata: dict = ...
    _original_node: Optional[VyperNode] = ...
    def __init__(self, parent: Optional[VyperNode] = ..., **kwargs: Any) -> None: ...
    def __hash__(self) -> Any: ...
    def __eq__(self, other: Any) -> Any: ...
    @property
    def description(self): ...
    @property
    def is_literal_value(self): ...
    @property
    def has_folded_value(self): ...
    @property
    def parent(self): ...
    @classmethod
    def get_fields(cls: Any) -> set: ...
    def set_parent(self, parent: VyperNode) -> VyperNode: ...
    def get_folded_value(self) -> ExprNode: ...
    def _set_folded_value(self, node: ExprNode) -> None: ...
    @classmethod
    def from_node(cls, node: VyperNode, **kwargs: Any) -> Any: ...
    def to_dict(self) -> dict: ...
    def get_children(
        self,
        node_type: Union[Type[VyperNode], Sequence[Type[VyperNode]], None] = ...,
        filters: Optional[dict] = ...,
        reverse: bool = ...,
    ) -> list: ...
    def get_descendants(
        self,
        node_type: Union[Type[VyperNode], Sequence[Type[VyperNode]], None] = ...,
        filters: Optional[dict] = ...,
        include_self: bool = ...,
        reverse: bool = ...,
    ) -> list: ...
    def get_ancestor(
        self, node_type: Union[Type[VyperNode], Sequence[Type[VyperNode]], None] = ...
    ) -> VyperNode: ...
    def get(self, field_str: str) -> Any: ...

class TopLevel(VyperNode):
    doc_string: Str = ...
    body: list = ...
    name: str = ...

class Module(TopLevel):
    path: str = ...
    resolved_path: str = ...
    def namespace(self) -> Any: ...  # context manager

class FunctionDef(TopLevel):
    args: arguments = ...
    decorator_list: list = ...
    returns: VyperNode = ...

class arguments(VyperNode):
    args: list = ...
    defaults: list = ...

class arg(VyperNode): ...
class Return(VyperNode): ...

class Log(VyperNode):
    value: Call = ...

class FlagDef(VyperNode):
    body: list = ...
    name: str = ...

class EventDef(VyperNode):
    body: list = ...
    name: str = ...

class InterfaceDef(VyperNode):
    body: list = ...
    name: str = ...

class StructDef(VyperNode):
    body: list = ...
    name: str = ...

class ExprNode(VyperNode):
    _expr_info: Any = ...

class Constant(ExprNode):
    value: Any = ...

class Num(Constant):
    @property
    def n(self): ...

class Int(Num):
    value: int = ...

class Decimal(Num): ...

class Hex(Num):
    @property
    def n_bytes(self): ...

class Str(Constant):
    @property
    def s(self): ...

class Bytes(Constant):
    @property
    def s(self): ...

class NameConstant(Constant): ...
class Ellipsis(Constant): ...

class List(VyperNode):
    elements: list = ...

class Tuple(VyperNode):
    elements: list = ...

class Dict(VyperNode):
    keys: list = ...
    values: list = ...

class Name(VyperNode):
    id: str = ...
    _type: str = ...

class Expr(VyperNode):
    value: ExprNode = ...

class ExtCall(VyperNode):
    value: Call = ...

class StaticCall(VyperNode):
    value: Call = ...

class UnaryOp(ExprNode):
    op: VyperNode = ...
    operand: ExprNode = ...

class USub(VyperNode): ...
class Not(VyperNode): ...

class BinOp(ExprNode):
    op: VyperNode = ...
    left: ExprNode = ...
    right: ExprNode = ...

class Add(VyperNode): ...
class Sub(VyperNode): ...
class Mult(VyperNode): ...
class Div(VyperNode): ...
class FloorDiv(VyperNode): ...
class Mod(VyperNode): ...
class Pow(VyperNode): ...
class LShift(VyperNode): ...
class RShift(VyperNode): ...
class BitAnd(VyperNode): ...
class BitOr(VyperNode): ...
class BitXor(VyperNode): ...

class BoolOp(ExprNode):
    op: VyperNode = ...
    values: list[ExprNode] = ...

class And(VyperNode): ...
class Or(VyperNode): ...

class Compare(ExprNode):
    op: VyperNode = ...
    left: ExprNode = ...
    right: ExprNode = ...

class Eq(VyperNode): ...
class NotEq(VyperNode): ...
class Lt(VyperNode): ...
class LtE(VyperNode): ...
class Gt(VyperNode): ...
class GtE(VyperNode): ...
class In(VyperNode): ...
class NotIn(VyperNode): ...

class Call(ExprNode):
    args: list = ...
    keywords: list = ...
    func: ExprNode = ...
    is_extcall: bool = ...
    is_staticcall: bool = ...
    is_plain_call: bool = ...
    kind_str: str = ...

class keyword(VyperNode): ...

class Attribute(ExprNode):
    attr: str = ...
    value: ExprNode = ...

class Subscript(ExprNode):
    slice: ExprNode = ...
    value: ExprNode = ...

class Assign(VyperNode): ...

class AnnAssign(VyperNode):
    target: Name = ...
    value: VyperNode = ...
    annotation: VyperNode = ...

class VariableDecl(VyperNode):
    target: Name = ...
    value: VyperNode = ...
    annotation: VyperNode = ...
    is_constant: bool = ...
    is_public: bool = ...
    is_immutable: bool = ...
    _expanded_getter: FunctionDef = ...

class AugAssign(VyperNode):
    op: VyperNode = ...
    target: ExprNode = ...
    value: ExprNode = ...

class Raise(VyperNode): ...
class Assert(VyperNode): ...
class Pass(VyperNode): ...

class Import(VyperNode):
    alias: str = ...
    name: str = ...

class ImportFrom(VyperNode):
    alias: str = ...
    level: int = ...
    module: str = ...
    name: str = ...

class ImplementsDecl(VyperNode):
    target: Name = ...
    annotation: Name = ...

class UsesDecl(VyperNode):
    annotation: VyperNode = ...

class InitializesDecl(VyperNode):
    annotation: VyperNode = ...

class ExportsDecl(VyperNode):
    annotation: VyperNode = ...

class If(VyperNode):
    body: list = ...
    orelse: list = ...

class IfExp(ExprNode):
    test: ExprNode = ...
    body: ExprNode = ...
    orelse: ExprNode = ...

class NamedExpr(ExprNode):
    target: Name = ...
    value: ExprNode = ...

class For(VyperNode):
    target: ExprNode
    iter: ExprNode
    body: list[VyperNode]

class Break(VyperNode): ...
class Continue(VyperNode): ...
