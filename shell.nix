{ pkgs ? import <nixpkgs> {} }:

pkgs.mkShell {
  buildInputs = with pkgs; [
    python313
    python313Packages.pip
    python313Packages.virtualenv
    stdenv.cc.cc.lib
  ];

  shellHook = ''
    export LD_LIBRARY_PATH="${pkgs.stdenv.cc.cc.lib}/lib:$LD_LIBRARY_PATH"

    if [ ! -d ".venv" ]; then
      python -m venv .venv
      source .venv/bin/activate
      pip install -e .
    else
      source .venv/bin/activate
    fi
  '';
}
