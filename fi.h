#ifndef __FI_H
#define __FI_H

// FI_MARK( <id> )
//
// Place a fault injection marker in the instruction stream. When running
// under spike with --fi-enable, this emits the marker's step counter value
// to stderr:
//
//   fi: mark => step = 215906, pc =            100d0, id = 1
//
// The <id> is a compile-time integer constant (-2048 to 2047) used to
// distinguish multiple markers.  Instructions following the marker start
// at step + 1; use --fi-trace to see exact instruction-to-step mapping
// near the marker.
//
// When fault injection is not enabled, this is a harmless NOP.

#define FI_MARK( id ) \
  __asm__ volatile ( "slti x0, x0, " #id ::: "memory" )

#endif
