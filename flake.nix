{
  description = "spike-fi: RISC-V Spike simulator with fault injection";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs }:
    let
      systems    = [ "x86_64-linux" "aarch64-linux" ];
      forAll     = nixpkgs.lib.genAttrs systems;
    in
    {
      # ======================================================================
      # Packages
      # ======================================================================
      packages = forAll (system:
        let
          pkgs = nixpkgs.legacyPackages.${system};

          # riscv64-none-elf-gcc has no multilib: it cannot target rv32 at all.
          # riscv32-none-elf-gcc is a native rv32 compiler and accepts
          # -march=rv32... -mabi=ilp32 without a multilib lookup.
          riscvCC       = pkgs.pkgsCross.riscv32-embedded.buildPackages.gcc;
          riscvBinutils = pkgs.pkgsCross.riscv32-embedded.buildPackages.binutils-unwrapped;

          # --------------------------------------------------------------------
          # spike-fi: riscv-isa-sim patched with fault-injection support
          # --------------------------------------------------------------------
          spike-fi = pkgs.stdenv.mkDerivation {
            pname   = "spike-fi";
            version = "0.1.0";

            src = pkgs.fetchFromGitHub {
              owner = "riscv";
              repo  = "riscv-isa-sim";
              rev   = "770ce31f7543f57472b35e66600085ea81184bb2";
              hash  = "sha256-L7EG17jRTU/eDDDcKDjmUGx3lKgz93rWkjRQfFprMAg=";
            };

            patches = [ "${self}/spike/spike.patch" ];

            nativeBuildInputs = with pkgs; [
              autoconf automake libtool dtc pkg-config
            ];
            buildInputs = with pkgs; [ boost libelf ];

            # spike's build system requires an out-of-source directory.
            # AX_BOOST_REGEX (bundled in this spike version) uses AC_CHECK_LIB
            # with the C language, so it can't link the C++ libboost_regex and
            # incorrectly reports the library as missing.  Passing
            # --with-boost-regex=boost_regex bypasses AC_CHECK_LIB and sets
            # BOOST_REGEX_LIB="-lboost_regex" directly; the actual build then
            # links as C++ via the normal Makefile, where libstdc++ is present.
            # Out-of-source build: configurePhase leaves CWD inside _build,
            # so subsequent phases just run make/make install from there.
            configurePhase = ''
              runHook preConfigure
              mkdir _build && cd _build
              ../configure \
                --prefix=$out \
                --target=riscv64-unknown-elf \
                --with-boost-regex=boost_regex
              runHook postConfigure
            '';

            buildPhase = ''
              runHook preBuild
              make -j''${NIX_BUILD_CORES:-4}
              runHook postBuild
            '';

            installPhase = ''
              runHook preInstall
              make install
              runHook postInstall
            '';
          };

          # --------------------------------------------------------------------
          # pk-fi: riscv-pk (proxy kernel) patched for spike-fi, rv32 build
          #
          # Installed under $out/riscv32-unknown-elf/ so that the path
          #   ${RISCV}/riscv32-unknown-elf/bin/pk
          # that the example Makefile expects is satisfied.
          # --------------------------------------------------------------------
          pk-fi = pkgs.pkgsCross.riscv32-embedded.stdenv.mkDerivation {
            pname   = "pk-fi";
            version = "0.1.0";

            src = pkgs.fetchFromGitHub {
              owner = "riscv";
              repo  = "riscv-pk";
              rev   = "9c61d29846d8521d9487a57739330f9682d5b542";
              hash  = "sha256-jYva0aIom809y02WgEYEdeqMMjh5DcvoFVzF3nyHqkw=";
            };

            patches = [ "${self}/spike/pk.patch" ];

            # autoconf/automake are build-machine tools; the cross stdenv
            # already sets CC/AR/RANLIB/etc. to the riscv32-none-elf toolchain,
            # whose libgcc.a is built for rv32 (no multilib mismatch).
            nativeBuildInputs = with pkgs; [ autoconf automake ];

            # pk requires an out-of-source build directory
            configurePhase = ''
              runHook preConfigure
              mkdir -p _build32
              cd _build32
              ../configure \
                --prefix=$out/riscv32-unknown-elf \
                --host=riscv32-none-elf \
                --with-arch=rv32gc \
                --with-abi=ilp32d
              cd ..
              runHook postConfigure
            '';

            buildPhase = ''
              runHook preBuild
              make -C _build32 -j''${NIX_BUILD_CORES:-4}
              runHook postBuild
            '';

            installPhase = ''
              runHook preInstall
              make -C _build32 install
              # Ensure pk is at the path spike expects regardless of what make install does.
              mkdir -p $out/riscv32-unknown-elf/bin
              cp -f _build32/pk $out/riscv32-unknown-elf/bin/pk
              runHook postInstall
            '';
          };

          # --------------------------------------------------------------------
          # riscv-gcc-unknown-elf: compiler wrappers + symlinks that expose the
          # cross-compiler under the riscv64-unknown-elf-* names the Makefiles expect.
          #
          # nixpkgs riscv32-none-elf GCC 15 ships rv32gc/ilp32d as its multilib
          # variant for hardware-FP targets, but NOT rv32gc/ilp32.  The example
          # Makefile requests -mabi=ilp32; the wrapper silently promotes that to
          # -mabi=ilp32d so the compiler finds its libraries.  For the example
          # program (integer arithmetic + printf, no float arguments) both ABIs
          # are behaviourally identical.
          # --------------------------------------------------------------------
          riscv-gcc-unknown-elf =
            let
              # Shell-script wrapper for each compiler driver:
              # passes all args through, replacing -mabi=ilp32 with -mabi=ilp32d.
              makeCompilerWrapper = tool:
                pkgs.writeShellScript "riscv64-unknown-elf-${tool}" ''
                  args=()
                  for a in "$@"; do
                    case "$a" in
                      -mabi=ilp32) args+=(-mabi=ilp32d) ;;
                      *) args+=("$a") ;;
                    esac
                  done
                  exec ${riscvCC}/bin/riscv32-none-elf-${tool} "''${args[@]}"
                '';
              gccWrapper = makeCompilerWrapper "gcc";
              gppWrapper = makeCompilerWrapper "g++";
              cppWrapper = makeCompilerWrapper "cpp";
            in
            pkgs.runCommand "riscv-gcc-unknown-elf" {} ''
              mkdir -p $out/bin

              # Compiler-driver wrappers (need the ABI flag adjustment)
              ln -s ${gccWrapper} $out/bin/riscv64-unknown-elf-gcc
              [ -e ${riscvCC}/bin/riscv32-none-elf-g++  ] && \
                ln -s ${gppWrapper} $out/bin/riscv64-unknown-elf-g++
              [ -e ${riscvCC}/bin/riscv32-none-elf-cpp  ] && \
                ln -s ${cppWrapper} $out/bin/riscv64-unknown-elf-cpp

              # Simple renames for every other tool (objcopy, objdump, ar, …)
              for exe in \
                  ${riscvCC}/bin/riscv32-none-elf-* \
                  ${riscvBinutils}/bin/riscv32-none-elf-*; do
                name=$(basename "$exe")
                newname="''${name/riscv32-none-elf/riscv64-unknown-elf}"
                [ -e "$out/bin/$newname" ] || ln -sf "$exe" "$out/bin/$newname"
              done
            '';

          # --------------------------------------------------------------------
          # riscv-tools: unified $RISCV tree assembled from the packages above.
          #
          # The merged layout is:
          #   bin/spike                          <- from spike-fi
          #   bin/riscv64-unknown-elf-gcc  ...   <- from riscv-gcc-unknown-elf
          #   riscv32-unknown-elf/bin/pk         <- from pk-fi
          # --------------------------------------------------------------------
          riscv-tools = pkgs.symlinkJoin {
            name  = "riscv-tools";
            paths = [ spike-fi pk-fi riscv-gcc-unknown-elf ];
          };

        in {
          inherit spike-fi pk-fi riscv-gcc-unknown-elf riscv-tools;
          default = riscv-tools;
        }
      );

      # ======================================================================
      # Dev shell
      #
      # Usage:
      #   nix develop
      #
      # Then, from inside the shell:
      #   make --directory="${REPO_HOME}/example" build
      #   make --directory="${REPO_HOME}/example" run
      #   make --directory="${REPO_HOME}/example" run FI="--fi-enable --fi-debug --fi-trace"
      # ======================================================================
      devShells = forAll (system:
        let
          pkgs     = nixpkgs.legacyPackages.${system};
          riscvTools = self.packages.${system}.riscv-tools;
        in {
          default = pkgs.mkShell {
            packages = with pkgs; [
              riscvTools
              gnumake
              git
              dtc   # spike invokes dtc at runtime to generate the platform device tree
            ];

            shellHook = ''
              export REPO_HOME="$(git -C "$PWD" rev-parse --show-toplevel 2>/dev/null || echo "$PWD")"
              export RISCV="${riscvTools}"

              echo "spike-fi dev shell ready"
              echo "  REPO_HOME = $REPO_HOME"
              echo "  RISCV     = $RISCV"
              echo ""
              echo "  make --directory=\"\$REPO_HOME/example\" build"
              echo "  make --directory=\"\$REPO_HOME/example\" run"
              echo "  make --directory=\"\$REPO_HOME/example\" run FI=\"--fi-enable --fi-debug --fi-trace\""
            '';
          };
        }
      );
    };
}
