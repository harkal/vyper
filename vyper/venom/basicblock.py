from enum import Enum, auto
from typing import TYPE_CHECKING, Any, Iterator, Optional

from vyper.utils import OrderedSet

# instructions which can terminate a basic block
BB_TERMINATORS = frozenset(["jmp", "jnz", "ret", "return", "revert", "deploy", "stop"])

VOLATILE_INSTRUCTIONS = frozenset(
    [
        "param",
        "alloca",
        "call",
        "staticcall",
        "invoke",
        "sload",
        "sstore",
        "iload",
        "istore",
        "assert",
        "mstore",
        "mload",
        "calldatacopy",
        "codecopy",
        "dloadbytes",
        "dload",
        "return",
        "ret",
        "jmp",
        "jnz",
    ]
)

CFG_ALTERING_OPS = frozenset(["jmp", "jnz", "call", "staticcall", "invoke", "deploy"])


if TYPE_CHECKING:
    from vyper.venom.function import IRFunction


class IRDebugInfo:
    """
    IRDebugInfo represents debug information in IR, used to annotate IR instructions
    with source code information when printing IR.
    """

    line_no: int
    src: str

    def __init__(self, line_no: int, src: str) -> None:
        self.line_no = line_no
        self.src = src

    def __repr__(self) -> str:
        src = self.src if self.src else ""
        return f"\t# line {self.line_no}: {src}".expandtabs(20)


class IROperand:
    """
    IROperand represents an operand in IR. An operand is anything that can
    be an argument to an IRInstruction
    """

    value: Any


class IRValue(IROperand):
    """
    IRValue represents a value in IR. A value is anything that can be
    operated by non-control flow instructions. That is, IRValues can be
    IRVariables or IRLiterals.
    """

    pass


class IRLiteral(IRValue):
    """
    IRLiteral represents a literal in IR
    """

    value: int

    def __init__(self, value: int) -> None:
        assert isinstance(value, str) or isinstance(value, int), "value must be an int"
        self.value = value

    def __repr__(self) -> str:
        return str(self.value)


class MemType(Enum):
    OPERAND_STACK = auto()
    MEMORY = auto()


class IRVariable(IRValue):
    """
    IRVariable represents a variable in IR. A variable is a string that starts with a %.
    """

    value: str
    offset: int = 0

    # some variables can be in memory for conversion from legacy IR to venom
    mem_type: MemType = MemType.OPERAND_STACK
    mem_addr: Optional[int] = None

    def __init__(
        self, value: str, mem_type: MemType = MemType.OPERAND_STACK, mem_addr: int = None
    ) -> None:
        assert isinstance(value, str)
        self.value = value
        self.offset = 0
        self.mem_type = mem_type
        self.mem_addr = mem_addr

    def __repr__(self) -> str:
        return self.value


class IRLabel(IROperand):
    """
    IRLabel represents a label in IR. A label is a string that starts with a %.
    """

    # is_symbol is used to indicate if the label came from upstream
    # (like a function name, try to preserve it in optimization passes)
    is_symbol: bool = False
    value: str

    def __init__(self, value: str, is_symbol: bool = False) -> None:
        assert isinstance(value, str), "value must be an str"
        self.value = value
        self.is_symbol = is_symbol

    def __repr__(self) -> str:
        return self.value


class IRInstruction:
    """
    IRInstruction represents an instruction in IR. Each instruction has an opcode,
    operands, and return value. For example, the following IR instruction:
        %1 = add %0, 1
    has opcode "add", operands ["%0", "1"], and return value "%1".

    Convention: the rightmost value is the top of the stack.
    """

    opcode: str
    volatile: bool
    operands: list[IROperand]
    output: Optional[IROperand]
    # set of live variables at this instruction
    liveness: OrderedSet[IRVariable]
    dup_requirements: OrderedSet[IRVariable]
    parent: Optional["IRBasicBlock"]
    fence_id: int
    annotation: Optional[str]

    def __init__(
        self,
        opcode: str,
        operands: list[IROperand] | Iterator[IROperand],
        output: Optional[IROperand] = None,
    ):
        assert isinstance(opcode, str), "opcode must be an str"
        assert isinstance(operands, list | Iterator), "operands must be a list"
        self.opcode = opcode
        self.volatile = opcode in VOLATILE_INSTRUCTIONS
        self.operands = [op for op in operands]  # in case we get an iterator
        self.output = output
        self.liveness = OrderedSet()
        self.dup_requirements = OrderedSet()
        self.parent = None
        self.fence_id = -1
        self.annotation = None

    def get_label_operands(self) -> list[IRLabel]:
        """
        Get all labels in instruction.
        """
        return [op for op in self.operands if isinstance(op, IRLabel)]

    def get_non_label_operands(self) -> list[IROperand]:
        """
        Get input operands for instruction which are not labels
        """
        return [op for op in self.operands if not isinstance(op, IRLabel)]

    def get_inputs(self) -> list[IRVariable]:
        """
        Get all input operands for instruction.
        """
        return [op for op in self.operands if isinstance(op, IRVariable)]

    def get_outputs(self) -> list[IROperand]:
        """
        Get the output item for an instruction.
        (Currently all instructions output at most one item, but write
        it as a list to be generic for the future)
        """
        return [self.output] if self.output else []

    def replace_operands(self, replacements: dict) -> None:
        """
        Update operands with replacements.
        replacements are represented using a dict: "key" is replaced by "value".
        """
        for i, operand in enumerate(self.operands):
            if operand in replacements:
                self.operands[i] = replacements[operand]

    def __repr__(self) -> str:
        s = ""
        if self.output:
            s += f"{self.output} = "
        opcode = f"{self.opcode} " if self.opcode != "store" else ""
        s += opcode
        operands = ", ".join(
            [(f"label %{op}" if isinstance(op, IRLabel) else str(op)) for op in self.operands]
        )
        s += operands

        if self.annotation:
            s += f" <{self.annotation}>"

        # if self.liveness:
        #     return f"{s: <30} # {self.liveness}"

        return s


class IRBasicBlock:
    """
    IRBasicBlock represents a basic block in IR. Each basic block has a label and
    a list of instructions, while belonging to a function.

    The following IR code:
        %1 = add %0, 1
        %2 = mul %1, 2
    is represented as:
        bb = IRBasicBlock("bb", function)
        r1 = bb.add_instruction("add", "%0", "1")
        r2 = bb.add_instruction("mul", r1, "2")

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

    def __init__(self, label: IRLabel, parent: "IRFunction") -> None:
        assert isinstance(label, IRLabel), "label must be an IRLabel"
        self.label = label
        self.parent = parent
        self.instructions = []
        self.cfg_in = OrderedSet()
        self.cfg_out = OrderedSet()
        self.out_vars = OrderedSet()

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

    @property
    def is_reachable(self) -> bool:
        return len(self.cfg_in) > 0

    def _append_instruction(self, instruction: IRInstruction) -> None:
        assert isinstance(instruction, IRInstruction), "instruction must be an IRInstruction"
        instruction.parent = self
        self.instructions.append(instruction)

    def add_instruction_no_return(self, opcode: str, *args) -> None:
        inst = IRInstruction(opcode, list(args))
        self._append_instruction(inst)

    def add_instruction(self, opcode: str, *args) -> IRVariable:
        ret = self.parent.get_next_variable()
        inst = IRInstruction(opcode, list(args), ret)
        self._append_instruction(inst)
        return ret

    def insert_instruction(self, instruction: IRInstruction, index: int) -> None:
        assert isinstance(instruction, IRInstruction), "instruction must be an IRInstruction"
        instruction.parent = self
        self.instructions.insert(index, instruction)

    def clear_instructions(self) -> None:
        self.instructions = []

    def replace_operands(self, replacements: dict) -> None:
        """
        Update operands with replacements.
        """
        for instruction in self.instructions:
            instruction.replace_operands(replacements)

    @property
    def is_terminated(self) -> bool:
        """
        Check if the basic block is terminal, i.e. the last instruction is a terminator.
        """
        # it's ok to return False here, since we use this to check
        # if we can/need to append instructions to the basic block.
        if len(self.instructions) == 0:
            return False
        return self.instructions[-1].opcode in BB_TERMINATORS

    def copy(self):
        bb = IRBasicBlock(self.label, self.parent)
        bb.instructions = self.instructions.copy()
        bb.cfg_in = self.cfg_in.copy()
        bb.cfg_out = self.cfg_out.copy()
        bb.out_vars = self.out_vars.copy()
        return bb

    def __repr__(self) -> str:
        s = (
            f"{repr(self.label)}:  IN={[bb.label for bb in self.cfg_in]}"
            f" OUT={[bb.label for bb in self.cfg_out]} => {self.out_vars} \n"
        )
        for instruction in self.instructions:
            s += f"    {instruction}\n"
        return s
