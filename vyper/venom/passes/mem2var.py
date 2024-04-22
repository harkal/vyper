from vyper.utils import OrderedSet
from vyper.venom.analysis import DFG, calculate_cfg, calculate_liveness
from vyper.venom.basicblock import IRBasicBlock, IRInstruction, IRVariable
from vyper.venom.dominators import DominatorTree
from vyper.venom.function import IRFunction
from vyper.venom.passes.base_pass import IRPass


class Mem2Var(IRPass):
    """
    This pass promoted memory operations to variable operations, when possible.
    It does yet do any memory aliasing analysis, so it is conservative.
    """

    ctx: IRFunction
    dom: DominatorTree
    defs: dict[IRVariable, OrderedSet[IRBasicBlock]]
    dfg: DFG

    def _run_pass(self, ctx: IRFunction, entry: IRBasicBlock, dfg: DFG) -> int:
        self.ctx = ctx
        self.dfg = dfg

        calculate_cfg(ctx)
        self.dom = DominatorTree.build_dominator_tree(ctx, entry)

        dfg = DFG.build_dfg(ctx)
        self.dfg = dfg

        calculate_liveness(ctx)

        self.var_name_count = 0
        for var, inst in dfg.outputs.items():
            if inst.opcode != "alloca":
                continue
            self._process_alloca_var(dfg, var)

        return 0

    def _process_alloca_var(self, dfg: DFG, var: IRVariable):
        """
        Process alloca allocated variable. If it is only used by mstore/mload/return
        instructions, it is promoted to a stack variable. Otherwise, it is left as is.
        """
        uses = dfg.get_uses(var)
        if all([inst.opcode == "mload" for inst in uses]):
            return
        elif all([inst.opcode == "mstore" for inst in uses]):
            return
        elif all([inst.opcode in ["mstore", "mload", "return"] for inst in uses]):
            var_name = f"addr{var.name}_{self.var_name_count}"
            self.var_name_count += 1
            for inst in uses:
                if inst.opcode == "mstore":
                    inst.opcode = "store"
                    inst.output = IRVariable(var_name)
                    inst.operands = [inst.operands[0]]
                elif inst.opcode == "mload":
                    inst.opcode = "store"
                    inst.operands = [IRVariable(var_name)]
                elif inst.opcode == "return":
                    bb = inst.parent
                    new_var = self.ctx.get_next_variable()
                    idx = bb.instructions.index(inst)
                    bb.insert_instruction(
                        IRInstruction("mstore", [IRVariable(var_name), inst.operands[1]], new_var),
                        idx,
                    )
                    inst.operands[1] = new_var