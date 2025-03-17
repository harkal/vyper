import contextlib
from typing import Optional

from vyper.venom.analysis import CFGAnalysis, DominatorTreeAnalysis, LivenessAnalysis
from vyper.venom.basicblock import IRBasicBlock, IRInstruction, ir_printer
from vyper.venom.passes.base_pass import IRPass


class MemoryAccess:
    """Base class for memory SSA nodes"""

    def __init__(self, version: int):
        self.version = version


class MemoryDef(MemoryAccess):
    """Represents a definition of memory state"""

    def __init__(self, version: int, store_inst: IRInstruction):
        super().__init__(version)
        self.store_inst = store_inst


class MemoryUse(MemoryAccess):
    """Represents a use of memory state"""

    def __init__(self, version: int, load_inst: IRInstruction):
        super().__init__(version)
        self.load_inst = load_inst
        self.reaching_def: Optional[MemoryAccess] = None


class MemoryPhi(MemoryAccess):
    """Represents a phi node for memory states"""

    def __init__(self, version: int, block: IRBasicBlock):
        super().__init__(version)
        self.block = block
        self.operands: list[tuple[MemoryDef, IRBasicBlock]] = []


class MemSSA(IRPass):
    """
    This pass converts memory/storage operations into Memory SSA form,
    tracking memory definitions and uses explicitly.
    """

    VALID_LOCATION_TYPES = {"memory", "storage"}

    def __init__(self, analyses_cache, function, location_type: str = "memory"):
        super().__init__(analyses_cache, function)
        if location_type not in self.VALID_LOCATION_TYPES:
            raise ValueError(f"location_type must be one of: {self.VALID_LOCATION_TYPES}")
        self.location_type = location_type
        self.load_op = "mload" if location_type == "memory" else "sload"
        self.store_op = "mstore" if location_type == "memory" else "sstore"

        # Memory SSA specific state
        self.next_version = 0
        self.memory_defs: dict[IRBasicBlock, list[MemoryDef]] = {}
        self.memory_uses: dict[IRBasicBlock, list[MemoryUse]] = {}
        self.memory_phis: dict[IRBasicBlock, MemoryPhi] = {}
        self.current_def: dict[IRBasicBlock, MemoryAccess] = {}

    def run_pass(self):
        # Request required analyses
        self.analyses_cache.request_analysis(CFGAnalysis)
        self.dom = self.analyses_cache.request_analysis(DominatorTreeAnalysis)
        self.analyses_cache.request_analysis(LivenessAnalysis)

        # Build initial memory SSA form
        self._build_memory_ssa()

        # Clean up unnecessary phi nodes
        self._remove_redundant_phis()

        # Invalidate liveness analysis
        self.analyses_cache.invalidate_analysis(LivenessAnalysis)

    def _build_memory_ssa(self):
        """Build the memory SSA form for the function"""
        # First pass: create defs and uses
        for bb in self.dom.dfs_walk:
            self._process_block_definitions(bb)

        # Second pass: insert phi nodes where needed
        self._insert_phi_nodes()

        # Third pass: connect uses to their reaching definitions
        self._connect_uses_to_defs()

    def _process_block_definitions(self, block: IRBasicBlock):
        """Process memory definitions and uses in a basic block"""
        for inst in block.instructions:
            if inst.opcode == self.store_op:
                mem_def = MemoryDef(self.next_version, inst)
                self.next_version += 1
                self.memory_defs.setdefault(block, []).append(mem_def)
                self.current_def[block] = mem_def

            elif inst.opcode == self.load_op:
                mem_use = MemoryUse(self.next_version, inst)
                self.next_version += 1
                self.memory_uses.setdefault(block, []).append(mem_use)

    def _insert_phi_nodes(self):
        """Insert phi nodes at appropriate points in the CFG"""
        worklist = list(self.memory_defs.keys())

        while worklist:
            block = worklist.pop()
            for frontier in self.dom.dominator_frontiers[block]:
                if frontier not in self.memory_phis:
                    phi = MemoryPhi(self.next_version, frontier)
                    # Add operands from each predecessor block
                    for pred in frontier.cfg_in:
                        reaching_def = self._get_reaching_def(pred)
                        if reaching_def:
                            phi.operands.append((reaching_def, pred))
                    self.next_version += 1
                    self.memory_phis[frontier] = phi
                    worklist.append(frontier)

    def _connect_uses_to_defs(self):
        """Connect memory uses to their reaching definitions"""
        for block in self.dom.dfs_walk:
            reaching_def = self._get_reaching_def(block)

            if block in self.memory_uses:
                for use in self.memory_uses[block]:
                    use.reaching_def = reaching_def

    def _get_reaching_def(self, block: IRBasicBlock) -> Optional[MemoryAccess]:
        """Get the reaching definition for a block"""
        if block in self.memory_phis:
            return self.memory_phis[block]

        if block in self.memory_defs:
            return self.memory_defs[block][-1]

        if block.cfg_in:
            # Get reaching def from immediate dominator
            idom = self.dom.immediate_dominators[block]
            return self._get_reaching_def(idom) if idom else None

        return None

    def _remove_redundant_phis(self):
        """Remove unnecessary phi nodes"""
        for phi in list(self.memory_phis.values()):
            if all(op[1] == phi for op in phi.operands):
                del self.memory_phis[phi.block]

    def _post_instruction(self, inst: IRInstruction) -> str:
        s = ""
        if inst.parent in self.memory_uses:
            for use in self.memory_uses[inst.parent]:
                if use.load_inst == inst:
                    s += f"\t!use: {use.reaching_def.version if use.reaching_def else None}"
        if inst.parent in self.memory_defs:
            for def_ in self.memory_defs[inst.parent]:
                if def_.store_inst == inst:
                    s += f"\t!def: {def_.version}"

        return s

    def _pre_block(self, bb: IRBasicBlock):
        s = ""
        if bb in self.memory_phis:
            phi = self.memory_phis[bb]
            s += f"    !phi: {phi.version} <- "
            s += ", ".join(f"{op[0].version} from @{op[1].label}" for op in phi.operands)
            s += "\n"
        return s

    @contextlib.contextmanager
    def print_context(self):
        ir_printer.set(self)
        yield
        ir_printer.set(None)
