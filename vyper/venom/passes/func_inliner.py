

from vyper.venom.passes.base_pass import IRPass


class FuncInlinerPass(IRPass):
    """
    This pass inlines functions into the call sites.
    """

    def run_pass(self):
        ctx = self.function.ctx
        func_call_counts = {fn: 0 for fn in ctx.functions}
        
        for bb in ctx.get_basic_blocks():
            for inst in bb.instructions:
                if inst.opcode == "invoke":
                    func_name = inst.operands[0]
                    func_call_counts[func_name] += 1

        print(func_call_counts)
        