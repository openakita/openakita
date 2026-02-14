PortAudio binaries
==================

This repository provides pre-compiled dynamic libraries for
[PortAudio](http://www.portaudio.com/).

DLLs for Windows (32-bit and 64-bit)
------------------------------------

There are two sets of DLL files,
one including the default host APIs,
namely MME, DirectSound, WDM/KS and WASAPI,
and another one (`*-asio.dll`) where ASIO is enabled as well.
For more information about the ASIO SDK see
http://www.steinberg.net/en/company/developers.html.

dylib for macOS (64-bit, "universal")
----------------------------------------

The file `libportaudio.dylib` is compatible both
with Intel (`x86_64`) and with Apple Silicon (`arm64`) CPUs.

Details
-------

All files were auto-created with Github Actions.
For all the details, see [the config file](.github/workflows/build-libs.yml).

Copyright
---------

* PortAudio by Ross Bencina and Phil Burk, MIT License.

* Steinberg Audio Stream I/O API by Steinberg Media Technologies GmbH.
