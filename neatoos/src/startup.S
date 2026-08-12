	.syntax unified
	.cpu arm926ej-s
	.arm

	.section .vectors, "ax", %progbits
	.global _vectors
_vectors:
	ldr pc, =_reset
	ldr pc, =_hang
	ldr pc, =_hang
	ldr pc, =_hang
	ldr pc, =_hang
	ldr pc, =_hang
	ldr pc, =_hang
	ldr pc, =_hang

	.section .text.startup, "ax", %progbits
	.global _reset
	.type _reset, %function
_reset:
	ldr sp, =_stack_top
	bl neatoos_main

	.global _hang
	.type _hang, %function
_hang:
	b _hang
