# RemoteTree

This Sublime Text 3 plugin allows you to browse directories on remote servers without need to download the whole file structure.

# Installation

1. Download this package as ZIP file, extract it.
2. Select *Preferences* → *Browse Packages…* in Sublime Text menu.
3. Copy *RemoteTree* directory to the directory containing all your packages.
4. Restart Sublime Text.

Alternatively, you can install this package via Package Control (not yet available).

# Usage

For now, this package supports two workflows:
1. You can connect to servers specified in the file RemoteTree.sublime-settings located in your user settings directory.
2. Or you can quickly connect to any server without saving auth data. All downloaded files will be stored to the temporary directory, and it will be erased when you'll close the remote tree.

To configure your list of servers, select *Remote* → *Edit Servers/Settings* in the menu. It will create a config file (if it does not exist), and fill it with following content:
```js
{
  "servers": [{
    // Used in menu and as tree panel title
    // "display_name": "My Server",
    
    // IP or hostname (Required)
    "host": "example.com",
    
    // Default is 22
    // "port": 22,
    
    // Root directory at the server ("/" by default)
    // "remote_path": "/",
    
    // Directory at this computer to map to remote root (Required)
    // When you edit files on server they will be downloaded here first.
    // All files saved in this directory (or its subdirectories) will be uploaded to the appropriate paths on the server.
    "local_path": "~/Sites/",

    // Username
    "user": "root",

    // Password (comment out if you're using private keys)
    "password": "",

    // Location of the private key file to use for authentication
    // Keys located at ~/.ssh are loaded automatically, you don't need to specify them here.
    // "ssh_key_file": "",

    // Password for the private key
    // "ssh_key_pass": "",

    // If true, ignores modification times of files and downloads them even if they are unchanged.
    // "always_download": false,
  }]
}
```

You need to fill at least "host", "local_path" and "password" (you need to comment this field out if you're using key files for authorisation).

After that you can save the file, select *Remote* → *Connect…* and select the newly added server from the list. RemoteTree should connect to it and present you a view with the remote file structure.

When you click any file in the remote tree, it will be downloaded to the corresponding subdirectory of "local_path" you specified. If file already exists and its modification time is not older than the remote file, it will not be downloaded again (unless you specified "always_download" option).

Each local file under "local_path" on save will be uploaded back to the server. The modification times are not checked, so be careful not to overwrite other changes.

You can open any number of remote trees (for different servers as well as for the same one). Connection to the remote server will be closed when you close the last opened remote tree connected to it.

If you restart Sublime Text, all remote trees should reconnect automatically.

If something goes wrong, check the console (Alt/Opt + `) — is there's some errors, report them as issues.
