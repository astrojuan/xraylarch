##
"""
Important note:
seeing errors in build with python setup.py py2exe?

move whole directory to a non-networked drive!

"""
##
from distutils.core import setup
# from setuptools import setup
import py2exe
import os
import shutil
import numpy
import scipy
import matplotlib
import h5py
import epics
import Image
import sqlalchemy
import wx

matplotlib.use('WXAgg')
import wxmplot

ca = epics.ca.initialize_libca()
mpl_data_files = matplotlib.get_py2exe_datafiles()

extra_files = ['inno_setup.iss', 'COPYING', 'README.txt']

apps = []
for script, iconfile in (('larch', 'larch.ico'),):
    apps.append({'script': 'larch', 'icon_resources': [(0, iconfile)]})
    extra_files.append(iconfile)

# pu2exe.org for options
py2exe_opts = {'optimize':1,
                'bundle_files':2,
               'includes': ['epics', 'ctypes', 'wx', 'ConfigParser',
                            'numpy', 'scipy', 'matplotlib', 'h5py',
                            'Image', 'MySQLdb', 'sqlite3', 'sqlalchemy',
                            'xdrlib', 'zlib', 'struct', 'datetime', 'xml',
                            're', 'warnings', 'json', 'wxmplot'
                            ],
               'packages': ['MySQLdb', 'sqlite3', 'sqlalchemy.dialects.mysql',
                            'sqlalchemy.dialects.sqlite',
                            'sqlalchemy.orm', 'sqlalchemy.pool',
                            'epics.ca', 'wx.lib', 'wx.lib.newevent',
                            'h5py', 'scipy.optimize', 'scipy.signal',
                            'numpy.random', 'xml.etree', 'xml.etree.cElementTree'], 

               'excludes': ['Tkinter', '_tkinter', 'Tkconstants', 'tcl',
                            '_imagingtk', 'PIL._imagingtk', 'ImageTk',
                            'PIL.ImageTk', 'FixTk''_gtkagg', '_tkagg',
                            'qt', 'PyQt4Gui', 'Carbon', 'email'],
               'dll_excludes': ['libgdk-win32-2.0-0.dll',
                                'libgobject-2.0-0.dll']
               }

# include matplotlib datafiles

setup(name = "Larch",
      #windows = apps,
      console = apps,
      options = {'py2exe': py2exe_opts},
      data_files = mpl_data_files)
 
for fname in extra_files:
    shutil.copy(fname, os.path.join('dist', fname))
