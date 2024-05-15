

from vyper.venom.passes.base_pass import IRPass


class FuncInlinerPass(IRPass):
    """
    This pass inlines functions into the call sites.
    """

    def run_pass(self):
        ctx = self.function.ctx
        func_call_sites = {fn: [] for fn in ctx.functions}
        
        for bb in ctx.get_basic_blocks():
            for inst in bb.instructions:
                if inst.opcode == "invoke":
                    func_name = inst.operands[0]
                    func_call_sites[func_name].append(inst)

        funcs = self._filter_candidates(func_call_sites)
        for func in funcs:
            self._inline_function(func, func_call_sites[func])
        
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
        bb = call_site.parent
        