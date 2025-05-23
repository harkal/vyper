from typing import Dict, List, Union

from vyper.venom.memory_location import MemoryLocation
from vyper.utils import OrderedSet
from vyper.venom.analysis import CFGAnalysis, DFGAnalysis, DominatorTreeAnalysis, MemSSA
from vyper.venom.analysis.mem_ssa import LiveOnEntry, MemSSAAbstract, MemoryDef, MemoryPhi, MemoryUse
from vyper.venom.basicblock import IRBasicBlock, IRInstruction, IRLiteral, IROperand, IRVariable
from vyper.venom.passes.base_pass import InstUpdater, IRPass


class RedundantLoadElimination(IRPass):
    """
    This pass eliminates redundant memory loads using Memory SSA analysis.

    The optimization works by:
    1. Computing available loads at each basic block by merging loads from predecessors
    2. Tracking memory definitions that may kill available loads
    3. Identifying redundant loads that can be replaced with previously loaded values
    4. Ensuring load availability across control flow paths
    """

    def __init__(self, analyses_cache, function):
        super().__init__(analyses_cache, function)
        # Maps instructions to their replacement operands
        self.replacements: Dict[IRInstruction, IROperand] = {}
        # Maps basic blocks to their available loads
        self.available_loads_per_block: Dict[IRBasicBlock, Dict[MemoryUse, IROperand]] = {}

    def run_pass(self):
        self.mem_ssa = self.analyses_cache.request_analysis(MemSSA)
        self.dfg = self.analyses_cache.request_analysis(DFGAnalysis)
        self.updater = InstUpdater(self.dfg)

        self.effective_reaching_defs = {}
        self.defs_to_uses: Dict[MemoryDef, List[MemoryUse]] = {}
        for mem_use in self.mem_ssa.get_memory_uses():
            if mem_use.inst.opcode != "mload":
                continue
            if isinstance(mem_use.inst.operands[0], IRVariable):
                continue
            mem_def = self._walk_for_effective_reaching_def(mem_use.reaching_def, mem_use.loc, OrderedSet())
            self.effective_reaching_defs[mem_use] = mem_def
            if mem_def not in self.defs_to_uses:
                self.defs_to_uses[mem_def] = []
            self.defs_to_uses[mem_def].append(mem_use)


        for mem_def in self.defs_to_uses:
            top_emitted: Dict[int, IRVariable] = {}
            for mem_use in self.defs_to_uses[mem_def]:
                offset = mem_use.loc.offset
                if offset not in top_emitted:
                    if isinstance(mem_def, MemoryPhi):
                        new_var = self.updater.add_before(mem_def.block.first_non_phi_instruction, "mload", [IRLiteral(offset)], annotation="[redundant load elimination]")
                    elif mem_def.is_live_on_entry:
                        new_var = self.updater.add_before(self.function.entry.first_non_phi_instruction, "mload", [IRLiteral(offset)], annotation="[redundant load elimination]")
                    else:
                        new_var = self.updater.add_after(mem_def.inst, "mload", [IRLiteral(offset)], annotation="[redundant load elimination]")                    
                    
                    top_emitted[offset] = new_var

                inst = mem_use.inst
                self.updater.update(inst, "store", [top_emitted[offset]], annotation="[redundant load elimination]")


        self.analyses_cache.invalidate_analysis(MemSSA)
            
    def _walk_for_effective_reaching_def(self, current: MemoryUse, query_loc: MemoryLocation, visited: OrderedSet[MemoryUse]) -> MemoryUse:
        while current is not None:
            if current in visited:
                break
            visited.add(current)
            
            if isinstance(current, MemoryDef):
                if self.mem_ssa.memalias.may_alias(query_loc, current.loc):
                    return current
            if isinstance(current, MemoryPhi):
                reaching_defs = []
                for access, _ in current.operands:
                    reaching_def = self._walk_for_effective_reaching_def(access, query_loc, visited)
                    if reaching_def:
                        reaching_defs.append(reaching_def)
                if len(reaching_defs) == 1:
                    return reaching_defs[0]
                return current
            
            current = current.reaching_def

        return MemSSAAbstract.live_on_entry
        
        

    
