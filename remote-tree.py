import sublime
import sublime_plugin
import sys 
import os
import threading
import re
import tempfile

sys.path.append(os.path.dirname(__file__))
import pysftp

servers = []
trees = []

class LoadSubtreeThread(threading.Thread):
	def __init__(self, tree, item):
		self.tree = tree
		self.item = item
		tree.loading += 1
		threading.Thread.__init__(self)
 
	def run(self):
		ensure_connection(self.tree.server)
		conn = self.tree.server['conn']
		conn.chdir(self.tree.server['remote_path'] + '/' + self.item['path'] + self.item['name'])
		childs = conn.listdir_attr()
		existing = {}
		if self.item['childs']:
			for child in self.item['childs']:
				existing[child['name']] = child
		self.item['dirs'] = []
		self.item['files'] = []
		for attr in childs:
			isdir = conn.isdir(self.tree.server['remote_path'] + '/' + self.item['path'] + self.item['name'] + '/' + attr.filename)
			if attr.filename in existing:
				child = existing[attr.filename]
				child['dir'] = isdir
				self.item['dirs' if isdir else 'files'].append(child)
			else:
				child = {
					'index': len(self.tree.list),
					'depth': self.item['depth'] + 1,
					'path': self.item['path'] + self.item['name'] + '/',
					'name': attr.filename,
					'dir': isdir,
					'expanded': False,
					'childs': None,
					'loading': False
				}
				self.tree.list.append(child)
				self.item['dirs' if isdir else 'files'].append(child)
		self.item['childs'] = self.item['dirs'] + self.item['files']
		self.item['loading'] = False
		self.tree.loading -= 1
		self.tree.rebuild_phantom()

class OpenFileThread(threading.Thread):
	def __init__(self, tree, item):
		self.tree = tree
		self.item = item
		tree.loading += 1
		threading.Thread.__init__(self)

	def download_callback_wrap(self, view, local_name, remote_name, loaded, total, repeat):
		sublime.set_timeout(lambda: self.download_callback(view, local_name, remote_name, loaded, total, repeat), 0)

	def download_callback(self, view, local_name, remote_name, loaded, total, repeat):
		view.window().status_message(
			'Downloading %s to %s… %s' % (
				remote_name,
				local_name,
				'%d%%' % (loaded * 100 / total) if loaded < total else 'Done!'))

		if loaded >= total and not repeat:
			# Make message stay a bit longer
			sublime.set_timeout(lambda: self.download_callback(view, local_name, remote_name, loaded, total, True), 3000)
			window = self.tree.view.window()
			
			(group, index) = window.get_view_index(self.tree.view)

			view = window.open_file(local_name)
			if window.num_groups() > 1:
				group = 1
				window.set_view_index(view, group, 0)

	def run(self):
		ensure_connection(self.tree.server)
		conn = self.tree.server['conn']

		local_path = self.tree.server['local_path'] + (
			'' if self.tree.server['local_path'].endswith(os.sep) or self.item['path'].startswith(os.sep) else os.sep) + (
			self.item['path']) + (
			'' if self.item['path'].endswith(os.sep) or self.item['name'].startswith(os.sep) else os.sep) + (
		 	self.item['name'])
		 	
		local_path = os.path.expanduser(local_path)
		remote_path = self.tree.server['remote_path'] + (
			'' if self.tree.server['remote_path'].endswith('/') or self.item['path'].startswith('/') else '/') + (
			self.item['path'][1:] if self.tree.server['remote_path'].endswith('/') and self.item['path'].startswith('/') else self.item['path']) + self.item['name']

		download = True

		# Check mtime first
		if os.path.isfile(local_path) and (not 'always_download' in self.tree.server or not self.tree.server['always_download']):
			local_mtime = os.stat(local_path).st_mtime
			remote_mtime = conn.stat(remote_path).st_mtime
			if local_mtime < remote_mtime:
				download = False

		if download:
			os.makedirs(os.path.dirname(local_path), exist_ok=True)
			conn.get(
				remote_path, 
				local_path, 
				preserve_mtime=True,
				callback=lambda loaded, total: self.download_callback_wrap(self.tree.view, local_path, remote_path, loaded, total, False))
		else:
			window = self.tree.view.window()
			(group, index) = window.get_view_index(self.tree.view)

			view = window.open_file(local_path)
			if window.num_groups() > 1:
				group = 1
				window.set_view_index(view, group, 0)
		

		self.item['loading'] = False
		self.tree.loading -= 1

		self.tree.rebuild_phantom()


class RemoteTree():
	phantom = None
	tree_view = None
	loading = 0
	loading_anim = 0

	def __init__(self, window, server, tree_view=None):
		global trees
		self.server = server
		server['trees'] = (server['trees'] + 1) if 'trees' in server else 1
		self.rebuild_lock = threading.Lock()


		# TODO: make all this optional

		orig_view = window.active_view()
		if not tree_view:
			window.set_sidebar_visible(False)
			layout = window.get_layout()
			if len(layout['cols']) < 3:
				# Add a vertical cell to the left
				layout['cols'] = [0, 0.2, 1]
				layout['cells'] = [[0, 0, 1, len(layout['rows']) - 1]] + [
					[cell[0] + 1, cell[1], cell[2] + 1, cell[3]] for cell in layout['cells']
				]
				window.set_layout(layout)
				for view in window.views():
					(group, index) = window.get_view_index(view)
					window.set_view_index(view, group + 1, index)
			elif layout['cols'][1] > 0.3:
				# Left column takes too much space - add new column anyway
				layout['cols'] = [0, min(0.2, layout['cols'][1] / 2.0)] + layout['cols'][1:]
				layout['cells'] = [[0, 0, 1, len(layout['rows']) - 1]] + [
					[cell[0] + 1, cell[1], cell[2] + 1, cell[3]] for cell in layout['cells']
				]
				window.set_layout(layout)
				for view in window.views():
					(group, index) = window.get_view_index(view)
					window.set_view_index(view, group + 1, index)

			tree_view = window.new_file()
			tree_view.settings().set('line_numbers', False)
			tree_view.settings().set('gutter', False)
			tree_view.settings().set('rulers', [])
			tree_view.settings().set('remote_server', self.server)
			tree_view.run_command('insert', { 'characters': self.server['remote_path'] })
			tree_view.set_read_only(True)
			tree_view.set_name(self.server['display_name'] if 'display_name' in self.server else self.server['host'])
			tree_view.set_scratch(True)
			if window.num_groups() > 1: 
				(group, index) = window.get_view_index(orig_view)
				if group != 0:
					group = 0
					window.set_view_index(tree_view, group, 0)
					window.focus_view(orig_view) # Um... Seems like a bug
		else:
			tree_view.erase_phantoms('remote_tree')

		self.view = tree_view
		self.phantom_set = sublime.PhantomSet(tree_view, 'remote_tree')

		trees.append(self) 

		self.tree = { 
			'index': 0,
			'depth': 0,
			'path': '',
			'name': '',
			'dir': True,
			'expanded': False,
			'childs': None,
			'loading': False
		}
		self.list = [self.tree]
		self.opened = None
		self.expand(0)

	def expand(self, index):
		item = self.list[index]
		item['expanded'] = not item['expanded']
		if item['expanded'] and not item['loading']: # and item['childs'] == None -- caching
			item['loading'] = True
			thread = LoadSubtreeThread(self, item)
			thread.start()
			self.rebuild_phantom()
		else:
			self.rebuild_phantom()

	def open(self, index):
		item = self.list[index]
		if item['loading']:
			return
		item['loading'] = True
		self.opened = item

		thread = OpenFileThread(self, item)
		thread.start()
		self.rebuild_phantom()

	def select(self, path):
		for item in self.list:
			if item['path'] + item['name'] == path:
				self.opened = item
				self.rebuild_phantom()
				return
		self.deselect()
		
	def deselect(self):
		if self.opened:
			self.opened = None
			self.rebuild_phantom()

	def on_click(self, url):
		comps = url.split('/')
		if comps[0] == 'open':
			self.open(int(comps[1]))
		else:
			self.expand(int(comps[1]))

	def rebuild_phantom(self):
		with self.rebuild_lock:
			html = '''<body id="tree">
<style>
	body {
		font-size: 12px;
		line-height: 16px;
	}
	.file a, .dir a {
		display: block;
		padding-left: 4px;
	}
	.dir a {
		padding-top: 1px;
		padding-bottom: 2px;
	}
	.dir a {
		text-decoration: none;
	}
	.file.active {
		background-color: color(var(--background) blend(var(--foreground) 80%));
		border-radius: 3px;
	}
	.file span {
		font-size: 7px; 
	}
	.file a {
		text-decoration: none;
		color: var(--foreground);
	}
</style>''' + ''.join(self.render_subtree(self.tree, [])) + '</body>'
			self.phantom = sublime.Phantom(sublime.Region(0), html, sublime.LAYOUT_BLOCK, on_navigate=self.on_click)
			self.phantom_set.update([self.phantom])
 
	def anim_loading(self):
		if self.loading > 0:
			self.loading_anim = (self.loading_anim + 1) % 8
			char = '⣾⣽⣻⢿⡿⣟⣯⣷'[self.loading_anim]
			html = '''<body id="loader">
<style>
	.spinner {
		display: inline;
		color: var(--redish);
	}
</style>
Loading <span class="spinner">%s</span></body>''' % char
			self.loader = sublime.Phantom(sublime.Region(len(self.server['remote_path'])), html, sublime.LAYOUT_INLINE)
			self.phantom_set.update([self.loader, self.phantom] if self.phantom else [self.loader])
			sublime.set_timeout(self.anim_loading, 100)
		else:
			print('removing anim')
			self.phantom_set.update([self.phantom] if self.phantom else [])

	def render_subtree(self, item, result):
		if not item['dir']:
			result.append('<div class="file{active}" style="margin-left: {margin}px"><a href=open/{index}><span>📄&nbsp;</span>{name}{loading}</a></div>'.format(
				active=' active' if item == self.opened else '',
				margin=(item['depth'] * 20) - 10,
				index=item['index'],
				name=item['name'],
				loading=' ⌛' if item['loading'] else ''))
			return result

		if item['depth'] > 0:
			result.append('<div class="dir" style="margin-left: {margin}px"><a href=expand/{index}>{sign}&nbsp;{name}{loading}</a></div>'.format(
				margin=(item['depth'] * 20) - 10,
				index=item['index'],
				name=item['name'],
				loading=' ⌛' if item['loading'] else '',
				sign='▼' if item['expanded'] else '▶'))

		if item['childs'] != None and item['expanded']:
			for child in item['childs']:
				self.render_subtree(child, result)

		return result

class EditServersCommand(sublime_plugin.WindowCommand):
	def init_settings(self, view):
		if not view.is_loading():
			view.run_command('append', { 'characters': '''{
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

		// Timeout in seconds (5 seconds by default), 0 to disable
		// "timeout": 5,
	}]
}''' })
		else:
			sublime.set_timeout(lambda: self.init_settings(view), 10)

	def run(self):
		path = os.path.join(sublime.packages_path(), 'User', 'RemoteTree.sublime-settings')
		exists = os.path.isfile(path)
		view = self.window.open_file(path)
		if not exists:
			self.init_settings(view)
		return
 
class ConnectCommand(sublime_plugin.WindowCommand):
	def run(self):
		global servers

		items = [
			[server['display_name'] + ' (' + server['host'] + ')' if 'display_name' in server else server['host'], server['local_path']] for server in servers
		]
		self.window.show_quick_panel(items, self.on_server_select)

	def is_enabled(self):
		global servers
		return bool(servers) and len(servers) > 0

	def on_server_select(self, index):
		global servers
		if index > -1:
			RemoteTree(self.window, servers[index])

class QuickConnectCommand(sublime_plugin.WindowCommand):
	def run(self):
		self.window.show_input_panel('Enter the hostname (user:pass@host):', '', self.on_done, None, None)

	def on_done(self, text):
		m = re.match(r'^((?P<user>[^:@]+)(:(?P<password>[^@]+))?@)?(?P<host>.+)$', text)
		if m:
			RemoteTree(self.window, {
				'host': m.group('host'),
				'display_name': m.group('host'),
				'user': m.group('user'),
				'password': m.group('password'),
				'local_path': tempfile.mkdtemp(),
				'remote_path': '/'
			})
 

class EventListener(sublime_plugin.EventListener):
	def on_activated(self, view):
		global trees
		fname = view.file_name()
 
		for tree in trees:
			prefix = os.path.join(tree.server['local_path'], '')
			if fname and fname.startswith(prefix):
				tree.select('/' + fname[len(prefix):])
			else:
				tree.deselect()

	def on_close(self, view):
		global trees
		for tree in trees:
			if tree.view == view:
				tree.server['trees'] -= 1
				if tree.server['trees'] < 1 and 'conn' in tree.server:
					print('Disconnecting %s…' % tree.server['host'])
					tree.server['conn'].close()
					del tree.server['conn']
		trees = [tree for tree in trees if tree.view != view]

	def upload_callback(self, view, local_name, remote_name, uploaded, total, repeat):
		#view.set_status(
		#	'zzz-remote-tree-save',
		#	'Uploading to %s… %s' % (
		#		remote_name, 
		#		'%d%%' % (uploaded * 100 / total) if uploaded < total else 'Done!'))
		#if uploaded >= total:
		#	sublime.set_timeout(lambda: view.erase_status('zzz-remote-tree-save'), 5000)

		view.window().status_message(
			'Uploading %s to %s… %s' % (
				local_name,
				remote_name, 
				'%d%%' % (uploaded * 100 / total) if uploaded < total else 'Done!'))

		if uploaded >= total and not repeat:
			# Make message stay a bit longer
			sublime.set_timeout(lambda: self.upload_callback(view, local_name, remote_name, uploaded, total, True), 3000)

	def on_post_save_async(self, view):
		global servers
		fname = view.file_name()
		if not fname:
			return

		if not servers:
			return

		for server in servers:
			prefix = os.path.join(server['local_path'], '')
			prefix = os.path.expanduser(prefix)
			if fname.startswith(prefix):
				ensure_connection(server)
				suffix = fname[len(prefix):]
				remote_path = server['remote_path'] + ('' if server['remote_path'].endswith('/') or suffix.startswith('/') else '/') + suffix
				server['conn'].makedirs(os.path.dirname(remote_path))
				server['conn'].put(
					fname,
					remote_path, 
					preserve_mtime=True,
					callback=lambda uploaded, total: self.upload_callback(view, fname, remote_path, uploaded, total, False))
				break

def ensure_connection(server):
	if 'conn' in server and server['conn'].sftp_client.get_channel() and server['conn'].sftp_client.get_channel().get_transport() and server['conn'].sftp_client.get_channel().get_transport().is_active():
		try:
			transport = server['conn'].sftp_client.get_channel().get_transport()
			transport.send_ignore()
			return
		except EOFError:
			print('Connection is broken')

	print('Connecting to %s…' % server['host'])
	cnopts = pysftp.CnOpts()
	cnopts.hostkeys = None
	server['conn'] = pysftp.Connection(
		server['host'], 
		port              =	server['port'] if 'port' in server else 22,
		username          =	server['user'] if 'user' in server else None,
		password          = server['password'] if 'password' in server else None,
		private_key       = server['ssh_key_file'] if 'ssh_key_file' in server else None,
		private_key_pass  = server['ssh_key_pass'] if 'ssh_key_pass' in server else None,
		cnopts = cnopts
	) 
	
	if not 'timeout' in server:
		server['conn'].timeout = 5 # 5 secs by default
	elif server['timeout'] == 0:
		server['conn'].timeout = None
	else:
		server['conn'].timeout = server['timeout']
 
def update_servers():
	global servers
	settings = sublime.load_settings('RemoteTree.sublime-settings')
	servers = settings.get('servers')

def plugin_loaded():
	global settings, servers
	settings = sublime.load_settings('RemoteTree.sublime-settings')
	settings.add_on_change('servers', update_servers)
	servers = settings.get('servers')

	for window in sublime.windows():
		for view in window.views():
			server = view.settings().get('remote_server')
			if server:
				RemoteTree(window, server, view)

def plugin_unloaded(): 
	global servers, trees
	for tree in trees:
		if 'conn' in tree.server:
			print('Disconnecting %s…' % tree.server['host'])
			tree.server['conn'].close()
			del tree.server['conn']

	if servers:
		for server in servers: # This should never happen: all servers should already be disconnected
			if 'conn' in server:
				print('Disconnecting %s…' % server['host'])
				server['conn'].close()
				del server['conn']