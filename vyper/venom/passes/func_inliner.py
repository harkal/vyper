

from vyper.venom.analysis.cfg import CFGAnalysis
from vyper.venom.basicblock import IRBasicBlock, IRInstruction
from vyper.venom.context import IRContext
from vyper.venom.passes.base_pass import IRPass


class FuncInlinerPass(IRPass):
    """
    This pass inlines functions into the call sites.
    """
    ctx: IRContext

    def run_pass(self):
        self.ctx = self.function.ctx
        func_call_sites = {fn: [] for fn in self.ctx.functions}
        
        for bb in self.ctx.get_basic_blocks():
            for inst in bb.instructions:
                if inst.opcode == "invoke":
                    func_name = inst.operands[0]
                    func_call_sites[func_name].append(inst)

        funcs = self._filter_candidates(func_call_sites)
        for func in funcs:
            self._inline_function(self.ctx.get_function(func), func_call_sites[func])

        if len(funcs) > 0:
            self.analyses_cache.invalidate_analysis(CFGAnalysis)
        
    def _filter_candidates(self, func_call_counts):
        """
        Filter candidates for inlining. This will become more sophisticated in the future.
        """
        return [fn for fn, call_sites in func_call_counts.items() if len(call_sites) == 1]
    
    def _inline_function(self, func, call_sites):
        """
        Inline function into call sites.
        """
        for call_site in call_sites:
            self._inline_call_site(func, call_site)

    def _inline_call_site(self, func, call_site):
        """
        Inline function into call site.
        """
        prefix = "copy_"
        call_site_bb = call_site.parent
        call_site_func = call_site_bb.parent

        call_site_return = IRBasicBlock(self.ctx.get_next_label(f"{prefix}inline_return"), call_site_bb.parent)
        call_idx = call_site_bb.instructions.index(call_site)
        
        for inst in call_site_bb.instructions[call_idx + 1:]:
            call_site_return.insert_instruction(inst)
        call_site_func.append_basic_block(call_site_return)

        func_copy = func.copy(prefix)

        for bb in func_copy.get_basic_blocks():
            call_site_func.append_basic_block(bb)
            bb.parent = call_site_func
            for inst in bb.instructions:
                if inst.opcode == "ret":
                    inst.opcode = "jmp"
                    inst.operands = [call_site_return.label]
    

        call_site_bb.instructions = call_site_bb.instructions[:call_idx]
        call_site_bb.append_instruction("jmp", func_copy.entry.label)


        