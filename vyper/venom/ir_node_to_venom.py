from typing import Optional

from vyper.codegen.context import VariableRecord
from vyper.codegen.core import is_array_like
from vyper.codegen.ir_node import IRnode
from vyper.evm.opcodes import get_opcodes
from vyper.exceptions import CompilerPanic
from vyper.ir.compile_ir import is_mem_sym, is_symbol
from vyper.semantics.types.function import ContractFunctionT
from vyper.utils import MemoryPositions, OrderedSet
from vyper.venom.basicblock import (
    IRBasicBlock,
    IRInstruction,
    IRLabel,
    IRLiteral,
    IROperand,
    IRVariable,
    MemType,
)
from vyper.venom.function import IRFunction

_BINARY_IR_INSTRUCTIONS = frozenset(
    [
        "eq",
        "gt",
        "lt",
        "slt",
        "sgt",
        "shr",
        "shl",
        "sar",
        "or",
        "xor",
        "and",
        "add",
        "sub",
        "mul",
        "div",
        "smul",
        "sdiv",
        "mod",
        "exp",
        "sha3",
        "sha3_64",
        "signextend",
    ]
)

# Instructions that are mapped to their inverse
INVERSE_MAPPED_IR_INSTRUCTIONS = {"ne": "eq", "le": "gt", "sle": "sgt", "ge": "lt", "sge": "slt"}

# Instructions that have a direct EVM opcode equivalent and can
# be passed through to the EVM assembly without special handling
PASS_THROUGH_INSTRUCTIONS = [
    "chainid",
    "basefee",
    "timestamp",
    "blockhash",
    "caller",
    "selfbalance",
    "calldatasize",
    "callvalue",
    "address",
    "origin",
    "codesize",
    "gas",
    "gasprice",
    "gaslimit",
    "returndatasize",
    "coinbase",
    "number",
    "iszero",
    "not",
    "calldataload",
    "extcodesize",
    "extcodehash",
    "balance",
]

SymbolTable = dict[str, Optional[IROperand]]


def _get_symbols_common(a: dict, b: dict) -> dict:
    ret = {}
    # preserves the ordering in `a`
    for k in a.keys():
        if k not in b:
            continue
        if a[k] == b[k]:
            continue
        ret[k] = a[k], b[k]
    return ret


# convert IRnode directly to venom
def ir_node_to_venom(ir: IRnode) -> IRFunction:
    ctx = IRFunction()
    _convert_ir_bb(ctx, ir, {}, OrderedSet(), {})

    # Patch up basic blocks. Connect unterminated blocks to the next with
    # a jump. terminate final basic block with STOP.
    for i, bb in enumerate(ctx.basic_blocks):
        if not bb.is_terminated:
            if i < len(ctx.basic_blocks) - 1:
                bb.append_instruction("jmp", ctx.basic_blocks[i + 1].label)
            else:
                bb.append_instruction("stop")

    return ctx


def _convert_binary_op(
    ctx: IRFunction,
    ir: IRnode,
    symbols: SymbolTable,
    variables: OrderedSet,
    allocated_variables: dict[str, IRVariable],
    swap: bool = False,
) -> Optional[IRVariable]:
    ir_args = ir.args[::-1] if swap else ir.args
    arg_0, arg_1 = _convert_ir_bb_list(ctx, ir_args, symbols, variables, allocated_variables)

    assert isinstance(ir.value, str)  # mypy hint
    return ctx.get_basic_block().append_instruction(ir.value, arg_1, arg_0)


def _append_jmp(ctx: IRFunction, label: IRLabel) -> None:
    ctx.get_basic_block().append_instruction("jmp", label)

    label = ctx.get_next_label()
    bb = IRBasicBlock(label, ctx)
    ctx.append_basic_block(bb)


def _new_block(ctx: IRFunction) -> IRBasicBlock:
    bb = IRBasicBlock(ctx.get_next_label(), ctx)
    bb = ctx.append_basic_block(bb)
    return bb


def _handle_self_call(
    ctx: IRFunction,
    ir: IRnode,
    symbols: SymbolTable,
    variables: OrderedSet,
    allocated_variables: dict[str, IRVariable],
) -> Optional[IRVariable]:
    func_t = ir.passthrough_metadata.get("func_t", None)
    args_ir = ir.passthrough_metadata["args_ir"]
    goto_ir = [ir for ir in ir.args if ir.value == "goto"][0]
    target_label = goto_ir.args[0].value  # goto
    return_buf = goto_ir.args[1]  # return buffer
    ret_args: list[IROperand] = [IRLabel(target_label)]  # type: ignore

    for arg in args_ir:
        if arg.is_literal:
            var = _get_variable_from_address(variables, arg.value)
            if var is None:
                ret = _convert_ir_bb(ctx, arg, symbols, variables, allocated_variables)
                ret_args.append(ret)
            else:
                if allocated_variables.get(var.name) is not None:
                    ret_args.append(allocated_variables.get(var.name))
                else:
                    ret = _convert_ir_bb(
                        ctx, arg._optimized, symbols, variables, allocated_variables
                    )
                    if arg.location and arg.location.load_op == "calldataload":
                        bb = ctx.get_basic_block()
                        ret = bb.append_instruction(arg.location.load_op, ret)
                    ret_args.append(ret)
        else:
            ret = _convert_ir_bb(ctx, arg._optimized, symbols, variables, allocated_variables)
            if arg.location and arg.location.load_op == "calldataload":
                bb = ctx.get_basic_block()
                ret = bb.append_instruction(arg.location.load_op, ret)
            ret_args.append(ret)

    bb = ctx.get_basic_block()
    do_ret = func_t.return_type is not None
    if do_ret:
        ret_args.append(return_buf.value)  # type: ignore
        invoke_ret = bb.append_invoke_instruction(ret_args, returns=True)  # type: ignore
        allocated_variables["return_buffer"] = invoke_ret  # type: ignore
        return invoke_ret
    else:
        bb.append_invoke_instruction(ret_args, returns=False)  # type: ignore
        return None


def _handle_internal_func(
    ctx: IRFunction,
    ir: IRnode,
    func_t: ContractFunctionT,
    symbols: SymbolTable,
    allocated_variables: dict[str, IRVariable],
) -> IRnode:
    bb = IRBasicBlock(IRLabel(ir.args[0].args[0].value, True), ctx)  # type: ignore
    bb = ctx.append_basic_block(bb)

    old_ir_mempos = 0
    old_ir_mempos += 64

    for arg in func_t.arguments:
        new_var = bb.append_instruction("param")
        symbols[f"&{old_ir_mempos}"] = new_var
        allocated_variables[arg.name] = new_var
        bb.instructions[-1].annotation = arg.name
        old_ir_mempos += 32  # arg.typ.memory_bytes_required

    # return buffer
    if func_t.return_type is not None:
        symbols["return_buffer"] = bb.append_instruction("param")
        bb.instructions[-1].annotation = "return_buffer"

    # return address
    symbols["return_pc"] = bb.append_instruction("param")
    bb.instructions[-1].annotation = "return_pc"

    return ir.args[0].args[2]


def _convert_ir_simple_node(
    ctx: IRFunction,
    ir: IRnode,
    symbols: SymbolTable,
    variables: OrderedSet,
    allocated_variables: dict[str, IRVariable],
) -> Optional[IRVariable]:
    args = [_convert_ir_bb(ctx, arg, symbols, variables, allocated_variables) for arg in ir.args]
    return ctx.get_basic_block().append_instruction(ir.value, *args)  # type: ignore


_break_target: Optional[IRBasicBlock] = None
_continue_target: Optional[IRBasicBlock] = None


def _get_variable_from_op(
    variables: OrderedSet[VariableRecord], allocated_variables: [], var: IRVariable
) -> VariableRecord:
    for name, ivar in allocated_variables.items():
        if var.name == name:
            for v in variables.keys():
                if v.name == ivar:
                    return v


def _get_variable_from_address(
    variables: OrderedSet[VariableRecord], addr: int
) -> Optional[VariableRecord]:
    assert isinstance(addr, int), "non-int address"
    for var in variables.keys():
        if var.location.name != "memory":
            continue
        if addr >= var.pos and addr < var.pos + var.size:  # type: ignore
            return var
    return None


def _get_variables_from_address_and_size(
    variables: OrderedSet[VariableRecord], addr: int, size: int
) -> list[VariableRecord]:
    assert isinstance(addr, int), "non-int address"
    addr_end = addr + size
    ret = []
    for var in variables.keys():
        if var.location.name != "memory":
            continue
        if var.pos >= addr and var.pos + var.size <= addr_end:  # type: ignore
            ret.append(var)
    return ret


def _append_return_for_stack_operand(
    ctx: IRFunction, symbols: SymbolTable, ret_ir: IRVariable, last_ir: IRVariable
) -> None:
    bb = ctx.get_basic_block()
    if isinstance(ret_ir, IRLiteral):
        sym = symbols.get(f"&{ret_ir.value}", None)
        new_var = bb.append_instruction("alloca", 32, ret_ir)
        bb.append_instruction("mstore", sym, new_var)  # type: ignore
    else:
        sym = symbols.get(ret_ir.value, None)
        if sym is None:
            # FIXME: needs real allocations
            new_var = bb.append_instruction("alloca", 32, 0)
            bb.append_instruction("mstore", ret_ir, new_var)  # type: ignore
        else:
            new_var = ret_ir
    bb.append_instruction("return", last_ir, new_var)  # type: ignore


def _convert_ir_bb_list(ctx, ir, symbols, variables, allocated_variables):
    ret = []
    for ir_node in ir:
        venom = _convert_ir_bb(ctx, ir_node, symbols, variables, allocated_variables)
        assert venom is not None, ir_node
        ret.append(venom)
    return ret


def _convert_ir_bb(ctx, ir, symbols, variables, allocated_variables):
    assert isinstance(ir, IRnode), ir
    assert isinstance(variables, OrderedSet)
    global _break_target, _continue_target

    frame_info = ir.passthrough_metadata.get("frame_info", None)
    if frame_info is not None:
        local_vars = OrderedSet[VariableRecord](frame_info.frame_vars.values())
        variables |= local_vars

    assert isinstance(variables, OrderedSet)

    if ir.value in _BINARY_IR_INSTRUCTIONS:
        return _convert_binary_op(
            ctx, ir, symbols, variables, allocated_variables, ir.value in ["sha3_64"]
        )

    elif ir.value in INVERSE_MAPPED_IR_INSTRUCTIONS:
        org_value = ir.value
        ir.value = INVERSE_MAPPED_IR_INSTRUCTIONS[ir.value]
        new_var = _convert_binary_op(ctx, ir, symbols, variables, allocated_variables)
        ir.value = org_value
        return ctx.get_basic_block().append_instruction("iszero", new_var)

    elif ir.value in PASS_THROUGH_INSTRUCTIONS:
        return _convert_ir_simple_node(ctx, ir, symbols, variables, allocated_variables)

    elif ir.value in ["pass", "stop", "return"]:
        pass
    elif ir.value == "deploy":
        ctx.ctor_mem_size = ir.args[0].value
        ctx.immutables_len = ir.args[2].value
        return None
    elif ir.value == "seq":
        # Special case when all args are mstores
        if len([arg for arg in ir.args if arg.value != "mstore"]) == 0:
            bb = ctx.get_basic_block()
            for ir_node in ir.args:  # NOTE: skip the last one
                ret = _convert_ir_bb(ctx, ir_node.args[1], symbols, variables, allocated_variables)
                bb.append_instruction("mstore", ret)

            return None

        func_t = ir.passthrough_metadata.get("func_t", None)
        if ir.is_self_call:
            return _handle_self_call(ctx, ir, symbols, variables, allocated_variables)
        elif func_t is not None:
            symbols = {}
            allocated_variables = {}
            variables = OrderedSet(
                {v: True for v in ir.passthrough_metadata["frame_info"].frame_vars.values()}
            )
            if func_t.is_internal:
                ir = _handle_internal_func(ctx, ir, func_t, symbols, allocated_variables)
            # fallthrough

        ret = None
        for ir_node in ir.args:
            ret = _convert_ir_bb(ctx, ir_node, symbols, variables, allocated_variables)

        return ret
    elif ir.value in ["staticcall", "call"]:  # external call
        idx = 0
        gas = _convert_ir_bb(ctx, ir.args[idx], symbols, variables, allocated_variables)
        address = _convert_ir_bb(ctx, ir.args[idx + 1], symbols, variables, allocated_variables)

        value = None
        if ir.value == "call":
            value = _convert_ir_bb(ctx, ir.args[idx + 2], symbols, variables, allocated_variables)
        else:
            idx -= 1

        argsOffset, argsSize, retOffset, retSize = _convert_ir_bb_list(
            ctx, ir.args[idx + 3 : idx + 7], symbols, variables, allocated_variables
        )

        if isinstance(argsOffset, IRLiteral):
            offset = int(argsOffset.value)
            addr = offset - 32 + 4 if offset > 0 else 0
            argsOffsetVar = symbols.get(f"&{addr}", None)
            if argsOffsetVar is None:
                argsOffsetVar = argsOffset
            elif isinstance(argsOffsetVar, IRVariable):
                argsOffsetVar.mem_type = MemType.MEMORY
                argsOffsetVar.mem_addr = addr
                argsOffsetVar.offset = 32 - 4 if offset > 0 else 0
            else:  # pragma: nocover
                raise CompilerPanic("unreachable")
        else:
            argsOffsetVar = argsOffset

        bb = ctx.get_basic_block()
        if isinstance(address, IRVariable):
            var = _get_variable_from_op(variables, allocated_variables, address)

        var = _get_variable_from_address(variables, offset)
        if var:
            bb.append_instruction("mstore", offset, allocated_variables[var.name])

        if ir.value == "call":
            args = [retSize, retOffset, argsSize, argsOffsetVar, value, address, gas]
        else:
            args = [retSize, retOffset, argsSize, argsOffsetVar, address, gas]

        return ctx.get_basic_block().append_instruction(ir.value, *args)
    elif ir.value == "if":
        cond = ir.args[0]

        # convert the condition
        cont_ret = _convert_ir_bb(ctx, cond, symbols, variables, allocated_variables)
        cond_block = ctx.get_basic_block()

        cond_symbols = symbols.copy()
        cond_variables = variables.copy()
        cond_allocated_variables = allocated_variables.copy()

        else_block = IRBasicBlock(ctx.get_next_label("else"), ctx)
        ctx.append_basic_block(else_block)

        # convert "else"
        else_ret_val = None
        if len(ir.args) == 3:
            else_ret_val = _convert_ir_bb(
                ctx, ir.args[2], cond_symbols, cond_variables, cond_allocated_variables
            )
            if isinstance(else_ret_val, IRLiteral):
                assert isinstance(else_ret_val.value, int)  # help mypy
                else_ret_val = ctx.get_basic_block().append_instruction("store", else_ret_val)

        else_block_finish = ctx.get_basic_block()

        # convert "then"
        then_block = IRBasicBlock(ctx.get_next_label("then"), ctx)
        ctx.append_basic_block(then_block)

        then_ret_val = _convert_ir_bb(
            ctx, ir.args[1], cond_symbols, cond_variables, cond_allocated_variables
        )
        if isinstance(then_ret_val, IRLiteral):
            then_ret_val = ctx.get_basic_block().append_instruction("store", then_ret_val)

        cond_block.append_instruction("jnz", cont_ret, then_block.label, else_block.label)

        then_block_finish = ctx.get_basic_block()

        # exit bb
        exit_bb = IRBasicBlock(ctx.get_next_label("if_exit"), ctx)
        exit_bb = ctx.append_basic_block(exit_bb)

        if_ret = ctx.get_next_variable()
        if then_ret_val is not None and else_ret_val is not None:
            then_block_finish.append_instruction("store", then_ret_val, ret=if_ret)
            else_block_finish.append_instruction("store", else_ret_val, ret=if_ret)

        if not else_block.is_terminated:
            else_block_finish.append_instruction("jmp", exit_bb.label)

        if not then_block.is_terminated:
            then_block_finish.append_instruction("jmp", exit_bb.label)

        return if_ret

    elif ir.value == "with":
        ret = _convert_ir_bb(
            ctx, ir.args[1], symbols, variables, allocated_variables
        )  # initialization

        # Handle with nesting with same symbol
        with_symbols = symbols.copy()
        with_allocated_variables = allocated_variables.copy()

        sym = ir.args[0]
        new_var = ctx.get_basic_block().append_instruction("store", ret)
        with_allocated_variables[sym.value] = new_var
        with_symbols[sym.value] = new_var

        return _convert_ir_bb(
            ctx, ir.args[2], with_symbols, variables, with_allocated_variables
        )  # body
    elif ir.value == "goto":
        _append_jmp(ctx, IRLabel(ir.args[0].value))
    elif ir.value == "djump":
        args = [_convert_ir_bb(ctx, ir.args[0], symbols, variables, allocated_variables)]
        for target in ir.args[1:]:
            args.append(IRLabel(target.value))
        ctx.get_basic_block().append_instruction("djmp", *args)
        _new_block(ctx)
    elif ir.value == "set":
        sym = ir.args[0]
        arg_1 = _convert_ir_bb(ctx, ir.args[1], symbols, variables, allocated_variables)
        new_var = ctx.get_basic_block().append_instruction("store", arg_1)  # type: ignore
        symbols[sym.value] = new_var

    elif ir.value == "calldatacopy":
        arg_0, arg_1, size = _convert_ir_bb_list(
            ctx, ir.args, symbols, variables, allocated_variables
        )

        new_v = arg_0
        bb = ctx.get_basic_block()
        if isinstance(arg_0, IRLiteral) and isinstance(size, IRLiteral):
            vars = _get_variables_from_address_and_size(
                variables, int(arg_0.value), int(size.value)
            )
            for var in vars:
                if allocated_variables.get(var.name, None) is None:
                    new_v = IRVariable(var.name)
                    ctx.get_basic_block().append_instruction("alloca", var.size, var.pos, ret=new_v)
                    allocated_variables[var.name] = new_v
                    symbols[f"&{var.pos}"] = new_v

        bb.append_instruction("calldatacopy", size, arg_1, arg_0)  # type: ignore

        return None
    elif ir.value == "codecopy":
        arg_0, arg_1, size = _convert_ir_bb_list(
            ctx, ir.args, symbols, variables, allocated_variables
        )

        ctx.get_basic_block().append_instruction("codecopy", size, arg_1, arg_0)  # type: ignore
    elif ir.value == "symbol":
        return IRLabel(ir.args[0].value, True)
    elif ir.value == "data":
        label = IRLabel(ir.args[0].value)
        ctx.append_data("dbname", [label])
        for c in ir.args[1:]:
            if isinstance(c, int):
                assert 0 <= c <= 255, "data with invalid size"
                ctx.append_data("db", [c])  # type: ignore
            elif isinstance(c, bytes):
                ctx.append_data("db", [c])  # type: ignore
            elif isinstance(c, IRnode):
                data = _convert_ir_bb(ctx, c, symbols, variables, allocated_variables)
                ctx.append_data("db", [data])  # type: ignore
    elif ir.value == "assert":
        arg_0 = _convert_ir_bb(ctx, ir.args[0], symbols, variables, allocated_variables)
        current_bb = ctx.get_basic_block()
        current_bb.append_instruction("assert", arg_0)
    elif ir.value == "label":
        label = IRLabel(ir.args[0].value, True)
        bb = ctx.get_basic_block()
        if not bb.is_terminated:
            bb.append_instruction("jmp", label)
        bb = IRBasicBlock(label, ctx)
        ctx.append_basic_block(bb)
        _convert_ir_bb(ctx, ir.args[2], symbols, variables, allocated_variables)
    elif ir.value == "exit_to":
        func_t = ir.passthrough_metadata.get("func_t", None)
        assert func_t is not None, "exit_to without func_t"

        bb = ctx.get_basic_block()
        if func_t.is_external:
            # Hardcoded contructor special case
            if func_t.name == "__init__":
                label = IRLabel(ir.args[0].value, True)
                bb.append_instruction("jmp", label)
                return None
            if func_t.return_type is None:
                bb.append_instruction("stop")
                return None
            else:
                last_ir = None
                ret_var = ir.args[1]
                deleted = None
                if ret_var.is_literal and symbols.get(f"&{ret_var.value}", None) is not None:
                    deleted = symbols[f"&{ret_var.value}"]
                    del symbols[f"&{ret_var.value}"]
                for arg in ir.args[2:]:
                    last_ir = _convert_ir_bb(ctx, arg, symbols, variables, allocated_variables)
                if deleted is not None:
                    symbols[f"&{ret_var.value}"] = deleted

                ret_ir = _convert_ir_bb(ctx, ret_var, symbols, variables, allocated_variables)

                bb = ctx.get_basic_block()

                var = (
                    _get_variable_from_address(variables, int(ret_ir.value))
                    if isinstance(ret_ir, IRLiteral)
                    else None
                )
                if var is not None:
                    allocated_var = allocated_variables.get(var.name, None)
                    assert allocated_var is not None, "unallocated variable"
                    new_var = symbols.get(f"&{ret_ir.value}", allocated_var)  # type: ignore

                    if var.size and int(var.size) > 32:
                        offset = int(ret_ir.value) - var.pos  # type: ignore
                        if offset > 0:
                            ptr_var = bb.append_instruction("add", var.pos, offset)
                        else:
                            ptr_var = allocated_var
                        bb.append_instruction("return", last_ir, ptr_var)
                    else:
                        _append_return_for_stack_operand(ctx, symbols, new_var, last_ir)
                else:
                    if isinstance(ret_ir, IRLiteral):
                        sym = symbols.get(f"&{ret_ir.value}", None)
                        if sym is None:
                            bb.append_instruction("return", last_ir, ret_ir)
                        else:
                            if func_t.return_type.memory_bytes_required > 32:
                                new_var = bb.append_instruction("alloca", 32, ret_ir)
                                bb.append_instruction("mstore", sym, new_var)
                                bb.append_instruction("return", last_ir, new_var)
                            else:
                                bb.append_instruction("return", last_ir, ret_ir)
                    else:
                        if last_ir and int(last_ir.value) > 32:
                            bb.append_instruction("return", last_ir, ret_ir)
                        else:
                            ret_buf = 128  # TODO: need allocator
                            new_var = bb.append_instruction("alloca", 32, ret_buf)
                            bb.append_instruction("mstore", ret_ir, new_var)
                            bb.append_instruction("return", last_ir, new_var)

                ctx.append_basic_block(IRBasicBlock(ctx.get_next_label(), ctx))

        elif func_t.is_internal:
            assert ir.args[1].value == "return_pc", "return_pc not found"
            if func_t.return_type is None:
                bb.append_instruction("ret", symbols["return_pc"])
            else:
                if func_t.return_type.memory_bytes_required > 32:
                    bb.append_instruction("ret", symbols["return_buffer"], symbols["return_pc"])
                else:
                    ret_by_value = bb.append_instruction("mload", symbols["return_buffer"])
                    bb.append_instruction("ret", ret_by_value, symbols["return_pc"])

    elif ir.value == "revert":
        arg_0, arg_1 = _convert_ir_bb_list(ctx, ir.args, symbols, variables, allocated_variables)
        ctx.get_basic_block().append_instruction("revert", arg_1, arg_0)

    elif ir.value == "dload":
        arg_0 = _convert_ir_bb(ctx, ir.args[0], symbols, variables, allocated_variables)
        bb = ctx.get_basic_block()
        src = bb.append_instruction("add", arg_0, IRLabel("code_end"))

        bb.append_instruction("dloadbytes", 32, src, MemoryPositions.FREE_VAR_SPACE)
        return bb.append_instruction("mload", MemoryPositions.FREE_VAR_SPACE)

    elif ir.value == "dloadbytes":
        dst, src_offset, len_ = _convert_ir_bb_list(
            ctx, ir.args, symbols, variables, allocated_variables
        )

        bb = ctx.get_basic_block()
        src = bb.append_instruction("add", src_offset, IRLabel("code_end"))
        bb.append_instruction("dloadbytes", len_, src, dst)
        return None

    elif ir.value == "mload":
        sym_ir = ir.args[0]
        var = (
            _get_variable_from_address(variables, int(sym_ir.value)) if sym_ir.is_literal else None
        )
        bb = ctx.get_basic_block()
        if var is not None:
            if var.size and var.size > 32:
                if is_array_like(var.typ):
                    return bb.append_instruction("store", var.pos)

                if allocated_variables.get(var.name, None) is None:
                    allocated_variables[var.name] = bb.append_instruction(
                        "alloca", var.size, var.pos
                    )

                offset = int(sym_ir.value) - var.pos
                if offset > 0:
                    ptr_var = bb.append_instruction("add", var.pos, offset)
                else:
                    ptr_var = allocated_variables[var.name]

                return bb.append_instruction("mload", ptr_var)
            else:
                if sym_ir.is_literal:
                    sym = symbols.get(f"&{sym_ir.value}", None)
                    if sym is None:
                        new_var = _convert_ir_bb(
                            ctx, sym_ir, symbols, variables, allocated_variables
                        )
                        if not isinstance(new_var, IRLiteral):
                            symbols[f"&{sym_ir.value}"] = new_var
                        if allocated_variables.get(var.name, None) is None:
                            allocated_variables[var.name] = new_var
                            return new_var
                        else:
                            return allocated_variables[var.name]
                    else:
                        return sym

                sym = symbols.get(f"&{sym_ir.value}", None)
                assert sym is not None, "unallocated variable"
                return sym
        else:
            if sym_ir.is_literal:
                new_var = symbols.get(f"&{sym_ir.value}", None)
                if new_var is not None:
                    return bb.append_instruction("mload", new_var)
                else:
                    return bb.append_instruction("mload", sym_ir.value)
            else:
                new_var = _convert_ir_bb(ctx, sym_ir, symbols, variables, allocated_variables)
                #
                # Old IR gets it's return value as a reference in the stack
                # New IR gets it's return value in stack in case of 32 bytes or less
                # So here we detect ahead of time if this mload leads a self call and
                # and we skip the mload
                #
                if sym_ir.is_self_call:
                    return new_var
                return ctx.get_basic_block().append_instruction("mload", new_var)

    elif ir.value == "mstore":
        sym_ir, arg_1 = _convert_ir_bb_list(ctx, ir.args, symbols, variables, allocated_variables)

        bb = ctx.get_basic_block()

        var = None
        if isinstance(sym_ir, IRLiteral):
            var = _get_variable_from_address(variables, int(sym_ir.value))

        if var is not None and var.size is not None:
            if var.size and var.size > 32:
                if allocated_variables.get(var.name, None) is None:
                    new_var = IRVariable(var.name)
                    allocated_variables[var.name] = new_var
                    bb.append_instruction("alloca", var.size, var.pos, ret=new_var)

                offset = int(sym_ir.value) - var.pos
                if offset > 0:
                    ptr_var = bb.append_instruction("add", var.pos, offset)
                else:
                    ptr_var = allocated_variables[var.name]

                bb.append_instruction("mstore", arg_1, ptr_var)
            else:
                if isinstance(sym_ir, IRLiteral):
                    new_var = IRVariable(var.name)
                    bb.append_instruction("store", arg_1, ret=new_var)
                    symbols[f"&{sym_ir.value}"] = new_var
                    allocated_variables[var.name] = new_var
                return new_var
        else:
            if not isinstance(sym_ir, IRLiteral):
                bb.append_instruction("mstore", arg_1, sym_ir)
                return None

            sym = symbols.get(f"&{sym_ir.value}", None)
            if sym is None:
                bb.append_instruction("mstore", arg_1, sym_ir)
                if arg_1 and not isinstance(sym_ir, IRLiteral):
                    symbols[f"&{sym_ir.value}"] = arg_1
                return None

            if isinstance(sym_ir, IRLiteral):
                bb.append_instruction("mstore", arg_1, sym)
                return None
            else:
                symbols[sym_ir.value] = arg_1
                return arg_1
    elif ir.value == "ceil32":
        x = ir.args[0]
        expanded = IRnode.from_list(["and", ["add", x, 31], ["not", 31]])
        return _convert_ir_bb(ctx, expanded, symbols, variables, allocated_variables)
    elif ir.value == "select":
        # b ^ ((a ^ b) * cond) where cond is 1 or 0
        cond, a, b = ir.args
        expanded = IRnode.from_list(["xor", b, ["mul", cond, ["xor", a, b]]])
        return _convert_ir_bb(ctx, expanded, symbols, variables, allocated_variables)

    elif ir.value in ["sload", "iload"]:
        arg_0 = _convert_ir_bb(ctx, ir.args[0], symbols, variables, allocated_variables)
        return ctx.get_basic_block().append_instruction(ir.value, arg_0)
    elif ir.value in ["sstore", "istore"]:
        arg_0, arg_1 = _convert_ir_bb_list(ctx, ir.args, symbols, variables, allocated_variables)
        ctx.get_basic_block().append_instruction(ir.value, arg_1, arg_0)
    elif ir.value == "unique_symbol":
        sym = ir.args[0]
        new_var = ctx.get_next_variable()
        symbols[f"&{sym.value}"] = new_var
        return new_var
    elif ir.value == "repeat":
        #
        # repeat(sym, start, end, bound, body)
        # 1) entry block         ]
        # 2) init counter block  ] -> same block
        # 3) condition block (exit block, body block)
        # 4) body block
        # 5) increment block
        # 6) exit block
        # TODO: Add the extra bounds check after clarify
        def emit_body_blocks():
            global _break_target, _continue_target
            old_targets = _break_target, _continue_target
            _break_target, _continue_target = exit_block, increment_block
            _convert_ir_bb(ctx, body, symbols.copy(), variables, allocated_variables.copy())
            _break_target, _continue_target = old_targets

        sym = ir.args[0]
        start, end, _ = _convert_ir_bb_list(
            ctx, ir.args[1:4], symbols, variables, allocated_variables
        )

        body = ir.args[4]

        entry_block = IRBasicBlock(ctx.get_next_label("repeat"), ctx)
        cond_block = IRBasicBlock(ctx.get_next_label("condition"), ctx)
        body_block = IRBasicBlock(ctx.get_next_label("body"), ctx)
        jump_up_block = IRBasicBlock(ctx.get_next_label("jump_up"), ctx)
        increment_block = IRBasicBlock(ctx.get_next_label("increment"), ctx)
        exit_block = IRBasicBlock(ctx.get_next_label("exit"), ctx)

        bb = ctx.get_basic_block()
        bb.append_instruction("jmp", entry_block.label)
        ctx.append_basic_block(entry_block)

        counter_var = ctx.get_basic_block().append_instruction("store", start)
        symbols[sym.value] = counter_var
        ctx.get_basic_block().append_instruction("jmp", cond_block.label)

        xor_ret = cond_block.append_instruction("xor", counter_var, end)
        cont_ret = cond_block.append_instruction("iszero", xor_ret)
        ctx.append_basic_block(cond_block)

        ctx.append_basic_block(body_block)
        emit_body_blocks()
        body_end = ctx.get_basic_block()

        if not body_end.is_terminated:
            body_end.append_instruction("jmp", jump_up_block.label)

        jump_up_block.append_instruction("jmp", increment_block.label)
        ctx.append_basic_block(jump_up_block)

        increment_block.insert_instruction(
            IRInstruction("add", [counter_var, IRLiteral(1)], counter_var)
        )

        increment_block.append_instruction("jmp", cond_block.label)
        ctx.append_basic_block(increment_block)

        ctx.append_basic_block(exit_block)

        cond_block.append_instruction("jnz", cont_ret, exit_block.label, body_block.label)
    elif ir.value == "cleanup_repeat":
        pass
    elif ir.value == "break":
        assert _break_target is not None, "Break with no break target"
        ctx.get_basic_block().append_instruction("jmp", _break_target.label)
        ctx.append_basic_block(IRBasicBlock(ctx.get_next_label(), ctx))
    elif ir.value == "continue":
        assert _continue_target is not None, "Continue with no contrinue target"
        ctx.get_basic_block().append_instruction("jmp", _continue_target.label)
        ctx.append_basic_block(IRBasicBlock(ctx.get_next_label(), ctx))
    elif ir.value == "gas":
        return ctx.get_basic_block().append_instruction("gas")
    elif ir.value == "returndatasize":
        return ctx.get_basic_block().append_instruction("returndatasize")
    elif ir.value == "returndatacopy":
        assert len(ir.args) == 3, "returndatacopy with wrong number of arguments"
        arg_0, arg_1, size = _convert_ir_bb_list(
            ctx, ir.args, symbols, variables, allocated_variables
        )

        new_var = ctx.get_basic_block().append_instruction("returndatacopy", arg_1, size)

        symbols[f"&{arg_0.value}"] = new_var
        return new_var
    elif ir.value == "selfdestruct":
        arg_0 = _convert_ir_bb(ctx, ir.args[0], symbols, variables, allocated_variables)
        ctx.get_basic_block().append_instruction("selfdestruct", arg_0)
    elif isinstance(ir.value, str) and ir.value.startswith("log"):
        args = reversed(
            [_convert_ir_bb(ctx, arg, symbols, variables, allocated_variables) for arg in ir.args]
        )
        topic_count = int(ir.value[3:])
        assert topic_count >= 0 and topic_count <= 4, "invalid topic count"
        ctx.get_basic_block().append_instruction("log", topic_count, *args)
    elif isinstance(ir.value, str) and ir.value.upper() in get_opcodes():
        _convert_ir_opcode(ctx, ir, symbols, variables, allocated_variables)
    elif isinstance(ir.value, str) and ir.value in symbols:
        return symbols[ir.value]
    elif ir.is_literal:
        return IRLiteral(ir.value)
    else:
        raise Exception(f"Unknown IR node: {ir}")

    return None


def _convert_ir_opcode(
    ctx: IRFunction,
    ir: IRnode,
    symbols: SymbolTable,
    variables: OrderedSet,
    allocated_variables: dict[str, IRVariable],
) -> None:
    opcode = ir.value.upper()  # type: ignore
    inst_args = []
    for arg in ir.args:
        if isinstance(arg, IRnode):
            inst_args.append(_convert_ir_bb(ctx, arg, symbols, variables, allocated_variables))
    ctx.get_basic_block().append_instruction(opcode, *inst_args)


def _data_ofst_of(sym, ofst, height_):
    # e.g. _OFST _sym_foo 32
    assert is_symbol(sym) or is_mem_sym(sym)
    if isinstance(ofst.value, int):
        # resolve at compile time using magic _OFST op
        return ["_OFST", sym, ofst.value]
    else:
        # if we can't resolve at compile time, resolve at runtime
        # ofst = _compile_to_assembly(ofst, withargs, existing_labels, break_dest, height_)
        return ofst + [sym, "ADD"]
