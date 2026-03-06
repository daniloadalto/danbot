{ pkgs }: {
  deps = [
    pkgs.python311
    pkgs.python311Packages.pip
    pkgs.git
    pkgs.gcc
    pkgs.openssl
  ];
}
