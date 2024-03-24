"""
"mem2stack" algorithm for vyper, basically takes allocas
and tries to elide them where possible
"""
from vyper.utils import OrderedSet
from vyper.venom.analysis import DFG, calculate_liveness, calculate_cfg
from vyper.venom.basicblock import BB_TERMINATORS, IRBasicBlock, IRInstruction, IRVariable
from vyper.venom.function import IRFunction
from vyper.venom.passes.base_pass import IRPass

PINNING_INSTRUCTIONS = frozenset(["call", "staticcall", "delegatecall", "return", "revert", "create", "create2"])

class Mem2Stack(IRPass):
    def _process_basic_block(self, bb: IRBasicBlock) -> None:
        self.ctx.append_basic_block(bb)

        instructions = bb.instructions.copy()
        bb.instructions.clear()

        for inst in instructions:
            if inst.opcode == "mload" and not self.pins[inst]:
                ptr = inst.operands[0]
                origin = self.dfg.get_producing_instruction(ptr)
                if origin is not None and origin.opcode == "alloca":
                    if ptr not in self.allocas:
                        # we haven't seen this location yet; it's ok, we allocate a virtual
                        # register, run the mload and then put the result of that into the
                        # virtual register
                        bb.instructions.append(inst)
                        self.allocas[ptr] = bb.append_instruction("store", inst.output)
                    else:
                        # assign the virtual register for this alloca to the output of mload
                        inst = IRInstruction("store", [self.allocas[ptr]], output=inst.output)
                        bb.instructions.append(inst)
                    continue
            elif inst.opcode == "mstore" and not self.pins[inst]:
                val, ptr = inst.operands
                origin = self.dfg.get_producing_instruction(ptr)
                if origin is not None and origin.opcode == "alloca":
                    if ptr in self.allocas:
                        # take the value and assign it into the virtual register for
                        # this alloca instead of actually running the mstore
                        # note this overwrites the virtual register. this will be fixed up in
                        # make_ssa.
                        inst = IRInstruction("store", [val], output=self.allocas[ptr])
                        bb.instructions.append(inst)
                    else:
                        # ditto, but also allocate a virtual register for this alloca
                        self.allocas[ptr] = bb.append_instruction("store", val)
                    continue

            bb.instructions.append(inst)

    # find allocas which are pinned - they are used by a
    # RETURN, CALL or REVERT instruction, so we should not elide
    # loads/stores. (can maybe do better, like only materialize the last
    # store before a pinning instruction).
    def _find_pins(self, ctx):
        self.pins = {}

        for bb in ctx.basic_blocks:
            for inst in bb.instructions:
                uses = self.dfg.get_uses(inst)
                pin = any(t.opcode in PINNING_INSTRUCTIONS for t in uses)
                self.pins[inst] = pin

    def _collect_allocas(self, ctx):
        self.allocas = {}

    def _run_pass(self, ctx: IRFunction) -> None:
        self.ctx = ctx
        self.dfg = DFG.build_dfg(ctx)

        self._find_pins(ctx)
        self._collect_allocas(ctx)

        basic_blocks = ctx.basic_blocks
        ctx.basic_blocks = []

        self.seen = {}
        for bb in basic_blocks:
            self._process_basic_block(bb)
