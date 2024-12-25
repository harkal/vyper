import json
import re
from typing import TYPE_CHECKING, Any, Iterator, Optional, Union

import vyper.venom.effects as effects
from vyper.codegen.ir_node import IRnode
from vyper.utils import OrderedSet

# instructions which can terminate a basic block
BB_TERMINATORS = frozenset(["jmp", "djmp", "jnz", "ret", "return", "stop", "exit"])

VOLATILE_INSTRUCTIONS = frozenset(
    [
        "param",
        "call",
        "staticcall",
        "delegatecall",
        "create",
        "create2",
        "invoke",
        "sload",
        "sstore",
        "iload",
        "istore",
        "tload",
        "tstore",
        "mstore",
        "mload",
        "calldatacopy",
        "mcopy",
        "extcodecopy",
        "returndatacopy",
        "codecopy",
        "dloadbytes",
        "dload",
        "return",
        "ret",
        "jmp",
        "jnz",
        "djmp",
        "log",
        "selfdestruct",
        "invalid",
        "revert",
        "assert",
        "assert_unreachable",
        "stop",
        "exit",
    ]
)

NO_OUTPUT_INSTRUCTIONS = frozenset(
    [
        "mstore",
        "sstore",
        "istore",
        "tstore",
        "dloadbytes",
        "calldatacopy",
        "mcopy",
        "returndatacopy",
        "codecopy",
        "extcodecopy",
        "return",
        "ret",
        "revert",
        "assert",
        "assert_unreachable",
        "selfdestruct",
        "stop",
        "invalid",
        "invoke",
        "jmp",
        "djmp",
        "jnz",
        "log",
        "exit",
    ]
)

assert VOLATILE_INSTRUCTIONS.issuperset(NO_OUTPUT_INSTRUCTIONS), (
    NO_OUTPUT_INSTRUCTIONS - VOLATILE_INSTRUCTIONS
)

CFG_ALTERING_INSTRUCTIONS = frozenset(["jmp", "djmp", "jnz"])

COMMUTATIVE_INSTRUCTIONS = frozenset(["add", "mul", "smul", "or", "xor", "and", "eq"])

COMPARATOR_INSTRUCTIONS = ("gt", "lt", "sgt", "slt")

if TYPE_CHECKING:
    from vyper.venom.function import IRFunction


class IRDebugInfo:
    """
    IRDebugInfo represents debug information in IR, used to annotate IR
    instructions with source code information when printing IR.
    """

    line_no: int
    src: str

    def __init__(self, line_no: int, src: str) -> None:
        self.line_no = line_no
        self.src = src

    def __repr__(self) -> str:
        src = self.src if self.src else ""
        return f"\t; line {self.line_no}: {src}".expandtabs(20)


class IROperand:
    """
    IROperand represents an IR operand. An operand is anything that can be
    operated by instructions. It can be a literal, a variable, or a label.
    """

    value: Any
    _hash: Optional[int] = None

    def __init__(self, value: Any) -> None:
        self.value = value
        self._hash = None

    @property
    def name(self) -> str:
        return self.value

    def __hash__(self) -> int:
        if self._hash is None:
            self._hash = hash(self.value)
        return self._hash

    def __eq__(self, other) -> bool:
        if not isinstance(other, type(self)):
            return False
        return self.value == other.value

    def __repr__(self) -> str:
        return str(self.value)


class IRLiteral(IROperand):
    """
    IRLiteral represents a literal in IR
    """

    value: int

    def __init__(self, value: int) -> None:
        assert isinstance(value, int), "value must be an int"
        super().__init__(value)


class IRVariable(IROperand):
    """
    IRVariable represents a variable in IR. A variable is a string that starts with a %.
    """

    _name: str
    version: Optional[int]

    def __init__(self, name: str, version: int = 0) -> None:
        assert isinstance(name, str)
        # TODO: allow version to be None
        assert isinstance(version, int)
        if not name.startswith("%"):
            name = f"%{name}"
        self._name = name
        self.version = version
        value = name
        if version > 0:
            value = f"{name}:{version}"
        super().__init__(value)

    @property
    def name(self) -> str:
        return self._name


class IRLabel(IROperand):
    """
    IRLabel represents a label in IR. A label is a string that starts with a %.
    """

    # is_symbol is used to indicate if the label came from upstream
    # (like a function name, try to preserve it in optimization passes)
    is_symbol: bool = False
    value: str

    def __init__(self, value: str, is_symbol: bool = False) -> None:
        assert isinstance(value, str), f"not a str: {value} ({type(value)})"
        assert len(value) > 0
        self.is_symbol = is_symbol
        super().__init__(value)

    _IS_IDENTIFIER = re.compile("[0-9a-zA-Z_]*")

    def __repr__(self):
        if self.__class__._IS_IDENTIFIER.fullmatch(self.value):
            return self.value

        return json.dumps(self.value)  # escape it


class IRInstruction:
    """
    IRInstruction represents an instruction in IR. Each instruction has an opcode,
    operands, and return value. For example, the following IR instruction:
        %1 = add %0, 1
    has opcode "add", operands ["%0", "1"], and return value "%1".

    Convention: the rightmost value is the top of the stack.
    """

    opcode: str
    operands: list[IROperand]
    output: Optional[IRVariable]
    # set of live variables at this instruction
    liveness: OrderedSet[IRVariable]
    parent: "IRBasicBlock"
    annotation: Optional[str]
    ast_source: Optional[IRnode]
    error_msg: Optional[str]

    def __init__(
        self,
        opcode: str,
        operands: list[IROperand] | Iterator[IROperand],
        output: Optional[IRVariable] = None,
    ):
        assert isinstance(opcode, str), "opcode must be an str"
        assert isinstance(operands, list | Iterator), "operands must be a list"
        self.opcode = opcode
        self.operands = list(operands)  # in case we get an iterator
        self.output = output
        self.liveness = OrderedSet()
        self.annotation = None
        self.ast_source = None
        self.error_msg = None

    @property
    def is_volatile(self) -> bool:
        return self.opcode in VOLATILE_INSTRUCTIONS

    @property
    def is_commutative(self) -> bool:
        return self.opcode in COMMUTATIVE_INSTRUCTIONS

    @property
    def is_comparator(self) -> bool:
        return self.opcode in COMPARATOR_INSTRUCTIONS

    @property
    def flippable(self) -> bool:
        return self.is_commutative or self.is_comparator

    @property
    def is_bb_terminator(self) -> bool:
        return self.opcode in BB_TERMINATORS

    @property
    def is_phi(self) -> bool:
        return self.opcode == "phi"

    @property
    def is_param(self) -> bool:
        return self.opcode == "param"

    @property
    def is_pseudo(self) -> bool:
        """
        Check if instruction is pseudo, i.e. not an actual instruction but
        a construct for intermediate representation like phi and param.
        """
        return self.is_phi or self.is_param

    def get_read_effects(self):
        return effects.reads.get(self.opcode, effects.EMPTY)

    def get_write_effects(self):
        return effects.writes.get(self.opcode, effects.EMPTY)

    def get_label_operands(self) -> Iterator[IRLabel]:
        """
        Get all labels in instruction.
        """
        return (op for op in self.operands if isinstance(op, IRLabel))

    def get_non_label_operands(self) -> Iterator[IROperand]:
        """
        Get input operands for instruction which are not labels
        """
        return (op for op in self.operands if not isinstance(op, IRLabel))

    def get_input_variables(self) -> Iterator[IRVariable]:
        """
        Get all input operands for instruction.
        """
        return (op for op in self.operands if isinstance(op, IRVariable))

    def get_outputs(self) -> list[IROperand]:
        """
        Get the output item for an instruction.
        (Currently all instructions output at most one item, but write
        it as a list to be generic for the future)
        """
        return [self.output] if self.output else []

    def make_nop(self):
        self.opcode = "nop"
        self.output = None
        self.operands = []
        self.annotation = None

    def flip(self):
        """
        Flip operands for commutative or comparator opcodes
        """
        assert self.flippable
        self.operands.reverse()

        if self.is_commutative:
            return

        if self.opcode in ("gt", "sgt"):
            self.opcode = self.opcode.replace("g", "l")
        else:
            self.opcode = self.opcode.replace("l", "g")

    def replace_operands(self, replacements: dict) -> None:
        """
        Update operands with replacements.
        replacements are represented using a dict: "key" is replaced by "value".
        """
        for i, operand in enumerate(self.operands):
            if operand in replacements:
                self.operands[i] = replacements[operand]

    def replace_label_operands(self, replacements: dict) -> None:
        """
        Update label operands with replacements.
        replacements are represented using a dict: "key" is replaced by "value".
        """
        replacements = {k.value: v for k, v in replacements.items()}
        for i, operand in enumerate(self.operands):
            if isinstance(operand, IRLabel) and operand.value in replacements:
                self.operands[i] = replacements[operand.value]

    @property
    def phi_operands(self) -> Iterator[tuple[IRLabel, IROperand]]:
        """
        Get phi operands for instruction.
        """
        assert self.opcode == "phi", "instruction must be a phi"
        for i in range(0, len(self.operands), 2):
            label = self.operands[i]
            var = self.operands[i + 1]
            assert isinstance(label, IRLabel), "phi operand must be a label"
            assert isinstance(
                var, (IRVariable, IRLiteral)
            ), "phi operand must be a variable or literal"
            yield label, var

    def remove_phi_operand(self, label: IRLabel) -> None:
        """
        Remove a phi operand from the instruction.
        """
        assert self.opcode == "phi", "instruction must be a phi"
        for i in range(0, len(self.operands), 2):
            if self.operands[i] == label:
                del self.operands[i : i + 2]
                return

    def get_ast_source(self) -> Optional[IRnode]:
        if self.ast_source:
            return self.ast_source
        idx = self.parent.instructions.index(self)
        for inst in reversed(self.parent.instructions[:idx]):
            if inst.ast_source:
                return inst.ast_source
        return self.parent.parent.ast_source

    def copy(self, prefix: str = "") -> "IRInstruction":
        ops: list[IROperand] = []
        for op in self.operands:
            if isinstance(op, IRLabel):
                ops.append(IRLabel(op.value))
            elif isinstance(op, IRVariable):
                ops.append(IRVariable(f"{prefix}{op.name}"))
            else:
                ops.append(IRLiteral(op.value))

        output = None
        if self.output:
            output = IRVariable(f"{prefix}{self.output.name}")

        inst = IRInstruction(self.opcode, ops, output)
        inst.parent = self.parent
        inst.liveness = self.liveness.copy()
        inst.annotation = self.annotation
        inst.ast_source = inst.ast_source
        inst.error_msg = inst.error_msg
        return inst

    def __repr__(self) -> str:
        s = ""
        if self.output:
            s += f"{self.output} = "
        opcode = f"{self.opcode} " if self.opcode != "store" else ""
        s += opcode
        operands = self.operands
        if self.opcode == "invoke":
            operands = [operands[0]] + list(reversed(operands[1:]))
        elif self.opcode not in ("jmp", "jnz", "phi"):
            operands = reversed(operands)  # type: ignore

        s += ", ".join([(f"@{op}" if isinstance(op, IRLabel) else str(op)) for op in operands])

        if self.annotation:
            s += f" ; {self.annotation}"

        return f"{s: <30}"


def _ir_operand_from_value(val: Any) -> IROperand:
    if isinstance(val, IROperand):
        return val

    assert isinstance(val, int), val
    return IRLiteral(val)


class IRBasicBlock:
    """
    IRBasicBlock represents a basic block in IR. Each basic block has a label and
    a list of instructions, while belonging to a function.

    The following IR code:
        %1 = add %0, 1
        %2 = mul %1, 2
    is represented as:
        bb = IRBasicBlock("bb", function)
        r1 = bb.append_instruction("add", "%0", "1")
        r2 = bb.append_instruction("mul", r1, "2")

    The label of a basic block is used to refer to it from other basic blocks
    in order to branch to it.

    The parent of a basic block is the function it belongs to.

    The instructions of a basic block are executed sequentially, and the last
    instruction of a basic block is always a terminator instruction, which is
    used to branch to other basic blocks.
    """

    label: IRLabel
    parent: "IRFunction"
    instructions: list[IRInstruction]
    # basic blocks which can jump to this basic block
    cfg_in: OrderedSet["IRBasicBlock"]
    # basic blocks which this basic block can jump to
    cfg_out: OrderedSet["IRBasicBlock"]
    # stack items which this basic block produces
    out_vars: OrderedSet[IRVariable]

    is_reachable: bool = False

    def __init__(self, label: IRLabel, parent: "IRFunction") -> None:
        assert isinstance(label, IRLabel), "label must be an IRLabel"
        self.label = label
        self.parent = parent
        self.instructions = []
        self.cfg_in = OrderedSet()
        self.cfg_out = OrderedSet()
        self.out_vars = OrderedSet()
        self.is_reachable = False

        self._garbage_instructions: set[IRInstruction] = set()

    def add_cfg_in(self, bb: "IRBasicBlock") -> None:
        self.cfg_in.add(bb)

    def remove_cfg_in(self, bb: "IRBasicBlock") -> None:
        assert bb in self.cfg_in
        self.cfg_in.remove(bb)

    def add_cfg_out(self, bb: "IRBasicBlock") -> None:
        # malformed: jnz condition label1 label1
        # (we could handle but it makes a lot of code easier
        # if we have this assumption)
        self.cfg_out.add(bb)

    def remove_cfg_out(self, bb: "IRBasicBlock") -> None:
        assert bb in self.cfg_out
        self.cfg_out.remove(bb)

    def append_instruction(
        self, opcode: str, *args: Union[IROperand, int], ret: Optional[IRVariable] = None
    ) -> Optional[IRVariable]:
        """
        Append an instruction to the basic block

        Returns the output variable if the instruction supports one
        """
        assert not self.is_terminated, self

        if ret is None:
            ret = self.parent.get_next_variable() if opcode not in NO_OUTPUT_INSTRUCTIONS else None

        # Wrap raw integers in IRLiterals
        inst_args = [_ir_operand_from_value(arg) for arg in args]

        inst = IRInstruction(opcode, inst_args, ret)
        inst.parent = self
        inst.ast_source = self.parent.ast_source
        inst.error_msg = self.parent.error_msg
        self.instructions.append(inst)
        return ret

    def append_invoke_instruction(
        self, args: list[IROperand | int], returns: bool
    ) -> Optional[IRVariable]:
        """
        Append an invoke to the basic block
        """
        assert not self.is_terminated, self
        ret = None
        if returns:
            ret = self.parent.get_next_variable()

        # Wrap raw integers in IRLiterals
        inst_args = [_ir_operand_from_value(arg) for arg in args]

        assert isinstance(inst_args[0], IRLabel), "Invoked non label"

        inst = IRInstruction("invoke", inst_args, ret)
        inst.parent = self
        inst.ast_source = self.parent.ast_source
        inst.error_msg = self.parent.error_msg
        self.instructions.append(inst)
        return ret

    def insert_instruction(self, instruction: IRInstruction, index: Optional[int] = None) -> None:
        assert isinstance(instruction, IRInstruction), "instruction must be an IRInstruction"

        if index is None:
            assert not self.is_terminated, (self, instruction)
            index = len(self.instructions)
        instruction.parent = self
        instruction.ast_source = self.parent.ast_source
        instruction.error_msg = self.parent.error_msg
        self.instructions.insert(index, instruction)

    def mark_for_removal(self, instruction: IRInstruction) -> None:
        self._garbage_instructions.add(instruction)

    def clear_dead_instructions(self) -> None:
        if len(self._garbage_instructions) > 0:
            self.instructions = [
                inst for inst in self.instructions if inst not in self._garbage_instructions
            ]
            self._garbage_instructions.clear()

    def remove_instruction(self, instruction: IRInstruction) -> None:
        assert isinstance(instruction, IRInstruction), "instruction must be an IRInstruction"
        self.instructions.remove(instruction)

    @property
    def phi_instructions(self) -> Iterator[IRInstruction]:
        for inst in self.instructions:
            if inst.opcode == "phi":
                yield inst
            else:
                return

    @property
    def non_phi_instructions(self) -> Iterator[IRInstruction]:
        return (inst for inst in self.instructions if inst.opcode != "phi")

    @property
    def param_instructions(self) -> Iterator[IRInstruction]:
        for inst in self.instructions:
            if inst.opcode == "param":
                yield inst
            else:
                return

    @property
    def pseudo_instructions(self) -> Iterator[IRInstruction]:
        return (inst for inst in self.instructions if inst.is_pseudo)

    @property
    def body_instructions(self) -> Iterator[IRInstruction]:
        return (inst for inst in self.instructions[:-1] if not inst.is_pseudo)

    def replace_operands(self, replacements: dict) -> None:
        """
        Update operands with replacements.
        """
        for instruction in self.instructions:
            instruction.replace_operands(replacements)

    def fix_phi_instructions(self):
        cfg_in_labels = tuple(bb.label for bb in self.cfg_in)

        needs_sort = False
        for inst in self.instructions:
            if inst.opcode != "phi":
                continue

            labels = inst.get_label_operands()
            for label in labels:
                if label not in cfg_in_labels:
                    needs_sort = True
                    inst.remove_phi_operand(label)

            op_len = len(inst.operands)
            if op_len == 2:
                inst.opcode = "store"
                inst.operands = [inst.operands[1]]
            elif op_len == 0:
                inst.opcode = "nop"
                inst.output = None
                inst.operands = []

        if needs_sort:
            self.instructions.sort(key=lambda inst: inst.opcode != "phi")

    def get_assignments(self):
        """
        Get all assignments in basic block.
        """
        return [inst.output for inst in self.instructions if inst.output]

    def get_uses(self) -> dict[IRVariable, OrderedSet[IRInstruction]]:
        uses: dict[IRVariable, OrderedSet[IRInstruction]] = {}
        for inst in self.instructions:
            for op in inst.get_input_variables():
                if op not in uses:
                    uses[op] = OrderedSet()
                uses[op].add(inst)
        return uses

    @property
    def is_empty(self) -> bool:
        """
        Check if the basic block is empty, i.e. it has no instructions.
        """
        return len(self.instructions) == 0

    @property
    def is_terminated(self) -> bool:
        """
        Check if the basic block is terminal, i.e. the last instruction is a terminator.
        """
        # it's ok to return False here, since we use this to check
        # if we can/need to append instructions to the basic block.
        if len(self.instructions) == 0:
            return False
        return self.instructions[-1].is_bb_terminator

    @property
    def is_terminal(self) -> bool:
        """
        Check if the basic block is terminal.
        """
        return len(self.cfg_out) == 0

    @property
    def liveness_in_vars(self) -> OrderedSet[IRVariable]:
        for inst in self.instructions:
            if inst.opcode != "phi":
                return inst.liveness
        return OrderedSet()

    def copy(self, prefix: str = "") -> "IRBasicBlock":
        new_label = IRLabel(f"{prefix}{self.label.value}")
        bb = IRBasicBlock(new_label, self.parent)
        bb.instructions = [inst.copy(prefix) for inst in self.instructions]
        for inst in bb.instructions:
            inst.parent = bb
        bb.cfg_in = OrderedSet()
        bb.cfg_out = OrderedSet()
        bb.out_vars = OrderedSet()
        return bb

    def __repr__(self) -> str:
        s = f"{self.label}:  ; IN={[bb.label for bb in self.cfg_in]}"
        s += f" OUT={[bb.label for bb in self.cfg_out]} => {self.out_vars}\n"
        for instruction in self.instructions:
            s += f"  {str(instruction).strip()}\n"
        if len(self.instructions) > 30:
            s += f"  ; {self.label}\n"
        if len(self.instructions) > 30 or self.parent.num_basic_blocks > 5:
            s += f"  ; ({self.parent.name})\n\n"
        return s
