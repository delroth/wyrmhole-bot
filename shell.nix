{ pkgs ? import <nixpkgs> {} }:

pkgs.mkShell {
  buildInputs = (with pkgs; [
    python39Full

    git
    zlib
  ]) ++ (with pkgs.python39Packages; [
    aiohttp
    av
    virtualenv
  ]);
}
