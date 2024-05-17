


from typing import Iterator
from vyper.venom.basicblock import IRInstruction
from vyper.utils import OrderedSet
from vyper.venom.function import IRFunction
from vyper.venom.context import IRContext
from vyper.venom.analysis.analysis import IRAnalysesCache, IRAnalysis


class FCGAnalysis(IRAnalysis):
    """
    Compute the function call graph for the context.
    """
    ctx: IRContext
    calls: dict[IRInstruction, OrderedSet[IRFunction]]

    def __init__(self, analyses_cache: IRAnalysesCache, function: IRFunction):
        super().__init__(analyses_cache, function)
        self.ctx = function.ctx
        self.calls = dict()

    def analyze(self) -> None:
        ctx = self.ctx
        fn = self.function
        for func in ctx.get_functions():
            self.calls[func] = OrderedSet()

        for fn in ctx.get_functions():
            self._analyze_function(fn)


    def get_calls(self, fn: IRFunction, no_self_calls=False) -> Iterator[IRFunction]:
        if no_self_calls:
            for fn in self.calls:
                if fn in self.calls[fn]:
                    del self.calls[fn]

        return self.calls[fn]

    def _analyze_function(self, fn: IRFunction) -> None:
        for bb in fn.get_basic_blocks():
            for inst in bb.instructions:
                if inst.opcode == "invoke":
                    callee = self.ctx.get_function(inst.operands[0])
                    self.calls[fn].add(callee)

    def invalidate(self):
        pass