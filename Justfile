build:
    flatpak-builder --force-clean --install-deps-from=flathub --repo=repo builddir com.t3tools.t3code.yml

build-bundle:
    flatpak build-bundle repo t3code.flatpak com.t3tools.t3code --runtime-repo=https://flathub.org/repo/flathub.flatpakrepo
