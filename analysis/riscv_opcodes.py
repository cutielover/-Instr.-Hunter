#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
RISC-V Opcode Reference

Provides opcode definitions and instruction encoding utilities
for analyzing RISC-V instructions.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.common import OPCODE_MAP, is_compressed


# Standard RV32I/RV64I opcodes
OPCODES = {
    opcode: {"name": name, "format": "?", "desc": "Shared opcode map"}
    for opcode, name in OPCODE_MAP.items()
}

# Branch funct3 values
BRANCH_FUNCT3 = {
    0b000: 'BEQ',
    0b001: 'BNE',
    0b100: 'BLT',
    0b101: 'BGE',
    0b110: 'BLTU',
    0b111: 'BGEU',
}

# Load funct3 values
LOAD_FUNCT3 = {
    0b000: 'LB',
    0b001: 'LH',
    0b010: 'LW',
    0b011: 'LD',
    0b100: 'LBU',
    0b101: 'LHU',
    0b110: 'LWU',
}

# Store funct3 values
STORE_FUNCT3 = {
    0b000: 'SB',
    0b001: 'SH',
    0b010: 'SW',
    0b011: 'SD',
}

# OP-IMM funct3 values
OP_IMM_FUNCT3 = {
    0b000: 'ADDI',
    0b001: 'SLLI',
    0b010: 'SLTI',
    0b011: 'SLTIU',
    0b100: 'XORI',
    0b101: 'SRLI/SRAI',
    0b110: 'ORI',
    0b111: 'ANDI',
}

# OP funct3 values (with funct7 distinction)
OP_FUNCT3 = {
    0b000: {'0': 'ADD', '1': 'SUB'},
    0b001: {'0': 'SLL'},
    0b010: {'0': 'SLT'},
    0b011: {'0': 'SLTU'},
    0b100: {'0': 'XOR'},
    0b101: {'0': 'SRL', '1': 'SRA'},
    0b110: {'0': 'OR'},
    0b111: {'0': 'AND'},
}

# System instructions by funct3
SYSTEM_FUNCT3 = {
    0b000: 'PRIV',   # ECALL, EBREAK, etc.
    0b001: 'CSRRW',
    0b010: 'CSRRS',
    0b011: 'CSRRC',
    0b101: 'CSRRWI',
    0b110: 'CSRRSI',
    0b111: 'CSRRCI',
}

# Compressed instruction quadrants
C_QUADRANT = {
    0b00: 'C.Q0',  # Load/Store stack-pointer
    0b01: 'C.Q1',  # Integer/control flow
    0b10: 'C.Q2',  # Misc/stack-pointer
}


def decode_instruction(encoding: int) -> dict:
    """
    Decode a RISC-V instruction encoding
    
    Returns a dictionary with:
    - opcode: The 7-bit opcode
    - format: Instruction format (R, I, S, B, U, J)
    - name: Instruction name if known
    - fields: Extracted bit fields
    """
    result = {
        'encoding': encoding,
        'compressed': False,
        'opcode': None,
        'format': None,
        'name': 'UNKNOWN',
        'fields': {}
    }
    
    # Check if compressed
    if is_compressed(encoding):
        result['compressed'] = True
        result['quadrant'] = encoding & 0x3
        result['funct3'] = (encoding >> 13) & 0x7
        result['name'] = f"C.{C_QUADRANT.get(result['quadrant'], 'UNK')}"
        return result
    
    # Standard 32-bit instruction
    opcode = encoding & 0x7F
    result['opcode'] = opcode
    
    if opcode in OPCODES:
        info = OPCODES[opcode]
        result['format'] = info['format']
        result['name'] = info['name']
        result['desc'] = info['desc']
    
    # Extract common fields
    result['fields'] = {
        'opcode': opcode,
        'rd': (encoding >> 7) & 0x1F,
        'funct3': (encoding >> 12) & 0x7,
        'rs1': (encoding >> 15) & 0x1F,
        'rs2': (encoding >> 20) & 0x1F,
        'funct7': (encoding >> 25) & 0x7F,
    }
    
    # Decode specific instruction based on opcode
    funct3 = result['fields']['funct3']
    funct7 = result['fields']['funct7']
    
    if opcode == 0b1100011:  # Branch
        if funct3 in BRANCH_FUNCT3:
            result['name'] = BRANCH_FUNCT3[funct3]
            
    elif opcode == 0b0000011:  # Load
        if funct3 in LOAD_FUNCT3:
            result['name'] = LOAD_FUNCT3[funct3]
            
    elif opcode == 0b0100011:  # Store
        if funct3 in STORE_FUNCT3:
            result['name'] = STORE_FUNCT3[funct3]
            
    elif opcode == 0b0010011:  # OP-IMM
        if funct3 in OP_IMM_FUNCT3:
            result['name'] = OP_IMM_FUNCT3[funct3]
            
    elif opcode == 0b0110011:  # OP
        if funct3 in OP_FUNCT3:
            funct7_bit = '1' if (funct7 & 0x20) else '0'
            if funct7_bit in OP_FUNCT3[funct3]:
                result['name'] = OP_FUNCT3[funct3][funct7_bit]
                
    elif opcode == 0b1110011:  # System
        if funct3 in SYSTEM_FUNCT3:
            result['name'] = SYSTEM_FUNCT3[funct3]
            
    return result


def format_instruction(encoding: int) -> str:
    """Format instruction as human-readable string"""
    info = decode_instruction(encoding)
    
    if info['compressed']:
        return f"0x{encoding:04x} [{info['name']}]"
    else:
        fields = info['fields']
        return (f"0x{encoding:08x} [{info['name']}] "
                f"rd={fields['rd']} rs1={fields['rs1']} rs2={fields['rs2']} "
                f"funct3={fields['funct3']} funct7={fields['funct7']}")


def print_opcode_table():
    """Print complete opcode reference table"""
    print("RISC-V Opcode Reference")
    print("=" * 70)
    print(f"{'Opcode':>10} {'Name':<12} {'Format':<6} {'Description'}")
    print("-" * 70)
    
    for opcode, info in sorted(OPCODES.items()):
        print(f"0b{opcode:07b}  {info['name']:<12} {info['format']:<6} {info['desc']}")


if __name__ == '__main__':
    import sys
    
    if len(sys.argv) > 1:
        # Decode provided instruction
        try:
            encoding = int(sys.argv[1], 0)
            print(format_instruction(encoding))
            info = decode_instruction(encoding)
            for key, value in info.items():
                print(f"  {key}: {value}")
        except ValueError:
            print(f"Invalid encoding: {sys.argv[1]}")
    else:
        print_opcode_table()
