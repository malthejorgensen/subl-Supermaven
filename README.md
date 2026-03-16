subl-Supermaven
===============

[Supermaven] plugin for [Sublime Text].

This is a port of the official Supermaven plugin for Neovim (<https://github.com/supermaven-inc/supermaven-nvim>).

How does it work?
-----------------
It starts by downloading the `sm-agent` binary from the URL (this will be an S3 bucket)

https://supermaven.com/api/download-path-v2?platform=macosx&arch=aarch64&editor=neovim

to `~/.supermaven/binary/v20/{os}-{platform}/`

Once downloaded you'll be asked to either signup or login to Supermaven or use the "Use Free Version" option.

After that, you're off to the races!

Key bindings
------------
- `tab` to accept the current completion
- `esc` to dismiss the current completion
- `ctrl+right` to accept the next word of the current completion


[Supermaven]: https://supermaven.com
[Sublime Text]: https://www.sublimetext.com/

<!-- [supermaven-nvim]: https://github.com/supermaven-inc/supermaven-nvim -->

See also
--------

- https://medium.com/@ndudfield/blast-from-the-past-ecae6fbad5fe (someone who tried to built this in 2024, but never finished)
