Github "Download ZIP" button is not supported
=============================================
Please use git clone instead.

The "Download ZIP" button on Github has two problems:

1. The archive does not contain Git metadata (.git directory) which
   is needed by OVIS when figuring out the version hashes and submodules.

2. It does not contain submodule sources.

Others have asked GitHub for a way to disable the zip portion of this 
button without result.  (see also project rose-compiler/rose for details.)

This README lives in a directory whose name causes it to appear
as close as possible to the "Download ZIP" button in the GitHub web
interface.
