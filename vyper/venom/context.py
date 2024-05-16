from typing import Iterator, Optional

from vyper.venom.basicblock import IRBasicBlock, IRInstruction, IRLabel, IROperand, IRVariable
from vyper.venom.function import IRFunction


class IRContext:
    functions: dict[IRLabel, IRFunction]
    ctor_mem_size: Optional[int]
    immutables_len: Optional[int]
    data_segment: list[IRInstruction]
    last_label: int
    last_variable: int

    def __init__(self) -> None:
        self.functions = {}
        self.ctor_mem_size = None
        self.immutables_len = None
        self.data_segment = []
        self.last_label = 0
        self.last_variable = 0

    def get_basic_blocks(self) -> Iterator[IRBasicBlock]:
        for fn in self.functions.values():
            for bb in fn.get_basic_blocks():
                yield bb

    def add_function(self, fn: IRFunction) -> None:
        fn.ctx = self
        self.functions[fn.name] = fn

    def remove_function(self, fn: IRFunction) -> None:
        del self.functions[fn.name]

    def create_function(self, name: str) -> IRFunction:
        label = IRLabel(name, True)
        if label in self.functions:
            return self.functions[label]
        fn = IRFunction(label, self)
        fn.append_basic_block(IRBasicBlock(label, fn))
        self.add_function(fn)
        return fn

    def get_function(self, name: IRLabel) -> IRFunction:
        if name in self.functions:
            return self.functions[name]
        raise Exception(f"Function {name} not found in context")

    def get_next_label(self, suffix: str = "") -> IRLabel:
        if suffix != "":
            suffix = f"_{suffix}"
        self.last_label += 1
        return IRLabel(f"{self.last_label}{suffix}")
    
    def get_next_variable(self) -> IRVariable:
        self.last_variable += 1
        return IRVariable(f"%{self.last_variable}")

    def get_last_variable(self) -> str:
        return f"%{self.last_variable}"


    def chain_basic_blocks(self) -> None:
        """
        Chain basic blocks together. This is necessary for the IR to be valid, and is done after
        the IR is generated.
        """
        for fn in self.functions.values():
            fn.chain_basic_blocks()

    def append_data(self, opcode: str, args: list[IROperand]) -> None:
        """
        Append data
        """
        self.data_segment.append(IRInstruction(opcode, args))  # type: ignore

    def as_graph(self) -> str:
        s = ["digraph G {"]
        for fn in self.functions.values():
            s.append(fn.as_graph(True))
            s.append("\n")
        s.append("}")
        return "\n".join(s)

    def __repr__(self) -> str:
        s = ["IRContext:"]
        for fn in self.functions.values():
            s.append(fn.__repr__())
            s.append("\n")

        if len(self.data_segment) > 0:
            s.append("\nData segment:")
            for inst in self.data_segment:
                s.append(f"{inst}")

        return "\n".join(s)
