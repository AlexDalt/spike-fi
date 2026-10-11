#include "test.h"

// note: the use of volatile might look wierd, but the intention is to 
// prevent gcc applying specific optimisations (e.g., loop unrolling).

volatile int A[ 10 ] = { 9, 8, 7, 6, 5, 4, 3, 2, 1, 0 };

int main( int argc, char* argv[] ) {
  int n = 10, t = 0;

  for( volatile int i = 0; i < n; i++ ) {
    t += A[ i ];
    FI_MARK( 0 );
  }

  printf( "%d\n", t );

  return 0;
}
