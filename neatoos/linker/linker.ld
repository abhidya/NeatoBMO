ENTRY(_reset)

/*
 * Default RAM origin is an offline build hypothesis, not a proven Neato load
 * address. Override with `make LDFLAGS_EXTRA=-Wl,--defsym=...` only after a
 * reviewed loader path exists.
 */
MEMORY
{
  RAM (rwx) : ORIGIN = 0x20000000, LENGTH = 64K
}

SECTIONS
{
  . = ORIGIN(RAM);

  .vectors ALIGN(4) :
  {
    KEEP(*(.vectors))
  } > RAM

  .text ALIGN(4) :
  {
    *(.text.startup)
    *(.text*)
    *(.rodata*)
  } > RAM

  .data ALIGN(4) :
  {
    *(.data*)
  } > RAM

  .bss ALIGN(4) (NOLOAD) :
  {
    __bss_start__ = .;
    *(.bss*)
    *(COMMON)
    __bss_end__ = .;
  } > RAM

  . = ALIGN(8);
  _stack_bottom = .;
  . = ORIGIN(RAM) + LENGTH(RAM);
  _stack_top = .;
}

