#!/usr/bin/env python
"""
GUI for displaying maps from HDF5 files

Needed Visualizations:

   2x2 grid:
     +-------------+--------------+
     | map1        |  2-color map |
     +-------------+--------------+
     | correlation |  map2        |
     +-------------+--------------+

   All subplots "live" so that selecting regions in
   any (via box or lasso) highlights other plots
         box in map:  show XRF spectra, highlight correlations
         lasso in correlations:  show XRF spectra, enhance map points
"""

__version__ = '5 (30-July-2013)'

import os
import sys
import time
from threading import Thread

import wx
import wx.lib.agw.flatnotebook as flat_nb
import wx.lib.scrolledpanel as scrolled
import wx.lib.mixins.inspection
from wx._core import PyDeadObjectError

import h5py
import numpy as np

from wxmplot import PlotFrame

import larch
from larch.utils.debugtime import debugtime

larch.use_plugin_path('wx')
larch.use_plugin_path('io')
larch.use_plugin_path('xrfmap')

larch.use_plugin_path('std')
from debugtime import DebugTimer

from xrfdisplay import XRFDisplayFrame
from mapimageframe import MapImageFrame

from wxutils import (SimpleText, EditableListBox, FloatCtrl,
                     Closure, pack, popup,
                     add_button, add_menu, add_choice)

from fileutils import nativepath

from xrm_mapfile import (GSEXRM_MapFile, GSEXRM_FileStatus,
                         GSEXRM_Exception, GSEXRM_NotOwner)

CEN = wx.ALIGN_CENTER|wx.ALIGN_CENTER_VERTICAL
LEFT = wx.ALIGN_LEFT|wx.ALIGN_CENTER_VERTICAL
RIGHT = wx.ALIGN_RIGHT|wx.ALIGN_CENTER_VERTICAL
ALL_CEN =  wx.ALL|CEN
ALL_LEFT =  wx.ALL|LEFT
ALL_RIGHT =  wx.ALL|RIGHT

FNB_STYLE = flat_nb.FNB_NO_X_BUTTON|flat_nb.FNB_SMART_TABS|flat_nb.FNB_NO_NAV_BUTTONS

FILE_WILDCARDS = "X-ray Maps (*.h5)|*.h5|All files (*.*)|*.*"

# FILE_WILDCARDS = "X-ray Maps (*.0*)|*.0&"

NOT_OWNER_MSG = """The File
   '%s'
appears to be open by another process.  Having two
processes writing to the file can cause corruption.

Do you want to take ownership of the file?
"""

NOT_GSEXRM_FILE = """The File
   '%s'
doesn't seem to be a Map File
"""

NOT_GSEXRM_FOLDER = """The Folder
   '%s'
doesn't seem to be a Map Folder
"""
FILE_ALREADY_READ = """The File
   '%s'
has already been read.
"""


def set_choices(choicebox, choices):
    index = 0
    try:
        current = choicebox.GetStringSelection()
        if current in choices:
            index = choices.index(current)
    except:
        pass
    choicebox.Clear()
    choicebox.AppendItems(choices)
    choicebox.SetStringSelection(choices[index])


class MapMathPanel(scrolled.ScrolledPanel):
    """Panel of Controls for doing math on arrays from Map data"""
    def __init__(self, parent, owner, **kws):
        scrolled.ScrolledPanel.__init__(self, parent, -1,
                                        style=wx.GROW|wx.TAB_TRAVERSAL, **kws)
        self.owner = owner
        sizer = wx.GridBagSizer(8, 9)
        self.show_new = add_button(self, 'Show New Map',     size=(125, -1),
                                   action=Closure(self.onShowMap, new=True))
        self.show_old = add_button(self, 'Replace Last Map', size=(125, -1),
                                   action=Closure(self.onShowMap, new=False))

        self.map_mode = add_choice(self, choices= ['Intensity', 'R, G, B'],
                                   size=(150, -1), action=self.onMode)

        self.expr_i = wx.TextCtrl(self, -1,   '', size=(150, -1))
        self.expr_r = wx.TextCtrl(self, -1,   '', size=(150, -1))
        self.expr_g = wx.TextCtrl(self, -1,   '', size=(150, -1))
        self.expr_b = wx.TextCtrl(self, -1,   '', size=(150, -1))

        ir = 0
        sizer.Add(SimpleText(self, 'Map Mode:'),    (ir, 0), (1, 1), ALL_CEN, 2)
        sizer.Add(self.map_mode,                    (ir, 1), (1, 1), ALL_LEFT, 2)
        txt = '''Enter Math Expressions for Map:
 a+b,  (a-b)/c, log10(a+0.1),  etc'''

        sizer.Add(SimpleText(self, txt),    (ir, 2), (2, 4), ALL_LEFT, 2)

        ir += 1
        sizer.Add(SimpleText(self, 'Intensity:'),    (ir, 0), (1, 1), ALL_CEN, 2)
        sizer.Add(self.expr_i,  (ir, 1), (1, 1), ALL_LEFT, 2)
        ir += 1
        sizer.Add(SimpleText(self, 'R, G, B:'),    (ir, 0), (1, 1), ALL_CEN, 2)

        box = wx.BoxSizer(wx.HORIZONTAL)
        box.Add(self.expr_r,  0, ALL_LEFT, 2)
        box.Add(self.expr_g,  0, ALL_LEFT, 2)
        box.Add(self.expr_b,  0, ALL_LEFT, 2)
        sizer.Add(box,  (ir, 1), (1, 5), ALL_LEFT, 2)

        ir += 1
        sizer.Add(self.show_new,  (ir, 0), (1, 2), ALL_LEFT, 2)
        sizer.Add(self.show_old,  (ir, 2), (1, 2), ALL_LEFT, 2)

        ir += 1
        sizer.Add(SimpleText(self, 'Name'),    (ir, 0), (1, 1), ALL_CEN, 2)
        sizer.Add(SimpleText(self, 'File'),        (ir, 1), (1, 1), ALL_CEN, 2)
        sizer.Add(SimpleText(self, 'ROI'),         (ir, 2), (1, 1), ALL_CEN, 2)
        sizer.Add(SimpleText(self, 'Detector'),    (ir, 3), (1, 1), ALL_CEN, 2)
        sizer.Add(SimpleText(self, 'DT Correct?'), (ir, 4), (1, 1), ALL_CEN, 2)

        self.varfile  = {}
        self.varroi   = {}
        self.varshape   = {}
        self.varrange   = {}
        self.vardet   = {}
        self.varcor   = {}
        for varname in ('a', 'b', 'c', 'd', 'e', 'f', 'g', 'h'):
            self.varfile[varname]   = vfile  = add_choice(self, choices=[], size=(200, -1),
                                                          action=Closure(self.onROI, varname=varname))
            self.varroi[varname]    = vroi   = add_choice(self, choices=[], size=(120, -1),
                                                          action=Closure(self.onROI, varname=varname))
            self.vardet[varname]    = vdet   = add_choice(self, choices=['sum', '1', '2', '3', '4'],  size=(70, -1))
            self.varcor[varname]    = vcor   = wx.CheckBox(self, -1, ' ')
            self.varshape[varname]  = vshape = SimpleText(self, 'Array Shape = (, )',
                                                          size=(200, -1))
            self.varrange[varname]  = vrange = SimpleText(self, 'Range = [   :    ]',
                                                          size=(200, -1))
            vcor.SetValue(1)
            vdet.SetSelection(0)

            ir += 1
            sizer.Add(SimpleText(self, "%s = " % varname),    (ir, 0), (1, 1), ALL_CEN, 2)
            sizer.Add(vfile,                        (ir, 1), (1, 1), ALL_CEN, 2)
            sizer.Add(vroi,                         (ir, 2), (1, 1), ALL_CEN, 2)
            sizer.Add(vdet,                         (ir, 3), (1, 1), ALL_CEN, 2)
            sizer.Add(vcor,                         (ir, 4), (1, 1), ALL_CEN, 2)
            ir +=1
            sizer.Add(vshape,                       (ir, 1), (1, 1), ALL_LEFT, 2)
            sizer.Add(vrange,                       (ir, 2), (1, 3), ALL_LEFT, 2)

        pack(self, sizer)
        self.SetupScrolling()
        self.onMode(evt=None, choice='int')

    def onMode(self, evt=None, choice=None):
        mode = self.map_mode.GetStringSelection()
        if choice is not None:
            mode = choice
        mode = mode.lower()
        self.expr_i.Disable()
        self.expr_r.Disable()
        self.expr_g.Disable()
        self.expr_b.Disable()
        if mode.startswith('i'):
            self.expr_i.Enable()
        else:
            self.expr_r.Enable()
            self.expr_g.Enable()
            self.expr_b.Enable()

    def onROI(self, evt, varname='a'):
        fname   = self.varfile[varname].GetStringSelection()
        roiname = self.varroi[varname].GetStringSelection()
        dname   = self.vardet[varname].GetStringSelection()
        dtcorr  = self.varcor[varname].IsChecked()
        det =  None
        if dname != 'sum':  det = int(dname)
        map = self.owner.filemap[fname].get_roimap(roiname, det=det, dtcorrect=dtcorr)
        self.varshape[varname].SetLabel('Array Shape = %s' % repr(map.shape))
        self.varrange[varname].SetLabel("Range = [%g: %g]" % (map.min(), map.max()))

    def set_roi_choices(self, rois):
        for wid in self.varroi.values():
            set_choices(wid, ['1'] + rois)

    def set_file_choices(self, fnames):
        for wid in self.varfile.values():
            set_choices(wid, fnames)

    def onShowMap(self, evt, new=True):
        mode = self.map_mode.GetStringSelection()
        def get_expr(wid):
            val = str(wid.Value)
            if len(val) == 0:
                val = '1'
            return val
        expr_i = get_expr(self.expr_i)
        expr_r = get_expr(self.expr_r)
        expr_g = get_expr(self.expr_g)
        expr_b = get_expr(self.expr_b)


        main_file = None
        _larch = self.owner.larch

        for varname in self.varfile.keys():
            fname   = self.varfile[varname].GetStringSelection()
            roiname = self.varroi[varname].GetStringSelection()
            dname   = self.vardet[varname].GetStringSelection()
            dtcorr  = self.varcor[varname].IsChecked()
            det =  None
            if dname != 'sum':  det = int(dname)
            if roiname == '1':
                map = 1
            else:
                map = self.owner.filemap[fname].get_roimap(roiname, det=det, dtcorrect=dtcorr)

            _larch.symtable.set_symbol(str(varname), map)
            if main_file is None:
                main_file = self.owner.filemap[fname]
        if mode.startswith('I'):
            map = _larch.eval(expr_i)
            info  = 'Intensity: [%g, %g]' %(map.min(), map.max())
            title = '%s: %s' % (fname, expr_i)
            subtitles = None
        else:
            rmap = _larch.eval(expr_r)
            gmap = _larch.eval(expr_g)
            bmap = _larch.eval(expr_b)
            map = np.array([rmap, gmap, bmap])
            map = map.swapaxes(0, 2).swapaxes(0, 1)
            title = '%s: (R, G, B) = (%s, %s, %s)' % (fname, expr_r, expr_g, expr_b)
            subtitles = {'red': expr_r, 'blue': expr_b, 'green': expr_g}
            info = ''
        try:
            x = main_file.get_pos(0, mean=True)
        except:
            x = None
        try:
            y = main_file.get_pos(1, mean=True)
        except:
            y = None

        fname = main_file.filename

        if len(self.owner.im_displays) == 0 or new:
            iframe = self.owner.add_imdisplay(title, det=None)
        self.owner.display_map(map, title=title, subtitles=subtitles,
                               info=info, x=x, y=y,
                               det=None, xrmfile=None)

class SimpleMapPanel(wx.Panel):
    """Panel of Controls for choosing what to display a simple ROI map"""

    def __init__(self, parent, owner, **kws):
        wx.Panel.__init__(self, parent, -1, **kws)
        self.owner = owner
        sizer = wx.GridBagSizer(8, 5)

        self.roi1 = add_choice(self, choices=[], size=(120, -1))
        self.roi2 = add_choice(self, choices=[], size=(120, -1))
        self.op   = add_choice(self, choices=['/', '*', '-', '+'], size=(80, -1))
        self.det  = add_choice(self, choices=['sum', '1', '2', '3', '4'],
                               size=(70, -1))

        self.cor  = wx.CheckBox(self, -1, 'Correct Deadtime?')
        self.cor.SetValue(1)
        self.op.SetSelection(0)
        self.det.SetSelection(0)
        self.show_new = add_button(self, 'Show New Map',     size=(125, -1),
                                   action=Closure(self.onShowMap, new=True))
        self.show_old = add_button(self, 'Replace Last Map', size=(125, -1),
                                   action=Closure(self.onShowMap, new=False))
        self.show_cor = add_button(self, 'Map1 vs. Map2', size=(125, -1),
                                   action=self.onShowCorrel)

        ir = 0
        sizer.Add(SimpleText(self, 'Detector'),          (ir, 0), (1, 1), ALL_CEN, 2)
        sizer.Add(SimpleText(self, 'Map 1'),             (ir, 1), (1, 1), ALL_CEN, 2)
        sizer.Add(SimpleText(self, 'Operator'),          (ir, 2), (1, 1), ALL_CEN, 2)
        sizer.Add(SimpleText(self, 'Map 2'),             (ir, 3), (1, 1), ALL_CEN, 2)

        ir += 1
        sizer.Add(self.det,           (ir, 0), (1, 1), ALL_CEN, 2)
        sizer.Add(self.roi1,          (ir, 1), (1, 1), ALL_CEN, 2)
        sizer.Add(self.op,            (ir, 2), (1, 1), ALL_CEN, 2)
        sizer.Add(self.roi2,          (ir, 3), (1, 1), ALL_CEN, 2)

        ir += 1
        sizer.Add(self.cor,       (ir,   0), (1, 2), ALL_LEFT, 2)
        sizer.Add(self.show_new,  (ir+1, 0), (1, 2), ALL_LEFT, 2)
        sizer.Add(self.show_old,  (ir+1, 2), (1, 2), ALL_LEFT, 2)
        sizer.Add(self.show_cor,  (ir+2, 0), (1, 2), ALL_LEFT, 2)
        pack(self, sizer)

    def onClose(self):
        for p in self.plotframes:
            try:
                p.Destroy()
            except:
                pass

    def onLasso(self, selected=None, mask=None, data=None, xrmfile=None, **kws):
        if xrmfile is None:
            xrmfile = self.owner.current_file
        ny, nx, npos = xrmfile.xrfmap['positions/pos'].shape
        # print 'on Lasso Shape = ', xrmfile, ny, nx
        # print selected
        indices = []
        for idx in selected:
            iy, ix = divmod(idx, ny)
            indices.append((ix, iy))
        # print indices

    def onShowCorrel(self, event=None):
        roiname1 = self.roi1.GetStringSelection()
        roiname2 = self.roi2.GetStringSelection()
        if roiname1 in ('', '1') or roiname2 in ('', '1'):
            return

        datafile  = self.owner.current_file
        det =self.det.GetStringSelection()
        if det == 'sum':
            det =  None
        else:
            det = int(det)
        dtcorrect = self.cor.IsChecked()

        map1 = datafile.get_roimap(roiname1, det=det, dtcorrect=dtcorrect).flatten()
        map2 = datafile.get_roimap(roiname2, det=det, dtcorrect=dtcorrect).flatten()

        path, fname = os.path.split(datafile.filename)
        title ='%s: %s vs %s' %(fname, roiname2, roiname1)
        pframe = PlotFrame(title=title, output_title=title)
        pframe.plot(map2, map1, xlabel=roiname2, ylabel=roiname1,
                    marker='o', markersize=4, linewidth=0)
        pframe.panel.cursor_mode = 'lasso'
        pframe.panel.lasso_callback = Closure(self.onLasso, xrmfile=datafile)

        pframe.Show()
        pframe.Raise()
        self.owner.plot_displays.append(pframe)


    def onShowMap(self, event=None, new=True):

        datafile  = self.owner.current_file
        det =self.det.GetStringSelection()
        if det == 'sum':
            det =  None
        else:
            det = int(det)

        dtcorrect = self.cor.IsChecked()
        roiname1 = self.roi1.GetStringSelection()
        roiname2 = self.roi2.GetStringSelection()

        map      = datafile.get_roimap(roiname1, det=det, dtcorrect=dtcorrect)
        title    = roiname1

        if roiname2 != '1':
            mapx = datafile.get_roimap(roiname2, det=det, dtcorrect=dtcorrect)
            op = self.op.GetStringSelection()
            if   op == '+': map +=  mapx
            elif op == '-': map -=  mapx
            elif op == '*': map *=  mapx
            elif op == '/': map /=  mapx

            title = "(%s) %s (%s)" % (roiname1, op, roiname2)

        try:
            x = datafile.get_pos(0, mean=True)
        except:
            x = None
        try:
            y = datafile.get_pos(1, mean=True)
        except:
            y = None

        pref, fname = os.path.split(datafile.filename)
        title = '%s: %s' % (fname, title)
        info  = 'Intensity: [%g, %g]' %(map.min(), map.max())

        if len(self.owner.im_displays) == 0 or new:
            iframe = self.owner.add_imdisplay(title, det=det)
        self.owner.display_map(map, title=title, info=info, x=x, y=y,
                               det=det, xrmfile=datafile)

    def set_roi_choices(self, rois):
        set_choices(self.roi1, rois)
        set_choices(self.roi2, ['1'] + rois)

class TriColorMapPanel(wx.Panel):
    """Panel of Controls for choosing what to display a 3 color ROI map"""
    def __init__(self, parent, owner, **kws):
        wx.Panel.__init__(self, parent, -1, **kws)
        self.owner = owner
        sizer = wx.GridBagSizer(8, 8)

        self.SetMinSize((425, 275))

        self.rchoice = add_choice(self, choices=[], size=(120, -1))
        # action=Closure(self.onSetRGBScale, color='r'))
        self.gchoice = add_choice(self, choices=[], size=(120, -1))
        # action=Closure(self.onSetRGBScale, color='g'))
        self.bchoice = add_choice(self, choices=[], size=(120, -1))
        # action=Closure(self.onSetRGBScale, color='b'))
        self.i0choice = add_choice(self, choices=[], size=(120, -1))
        # action=Closure(self.onSetRGBScale, color='i0'))

        self.show_new = add_button(self, 'Show New Map',     size=(125, -1),
                               action=Closure(self.onShow3ColorMap, new=True))
        self.show_old = add_button(self, 'Replace Last Map', size=(125, -1),
                               action=Closure(self.onShow3ColorMap, new=False))


        self.det  = add_choice(self, choices=['sum', '1', '2', '3', '4'], size=(70, -1))
        self.cor  = wx.CheckBox(self, -1, 'Correct Deadtime?')
        self.cor.SetValue(1)

        #self.rauto = wx.CheckBox(self, -1, 'Autoscale?')
        #self.gauto = wx.CheckBox(self, -1, 'Autoscale?')
        #self.bauto = wx.CheckBox(self, -1, 'Autoscale?')
        #self.rauto.SetValue(1)
        #self.gauto.SetValue(1)
        #self.bauto.SetValue(1)
        #self.rauto.Bind(wx.EVT_CHECKBOX, Closure(self.onAutoScale, color='r'))
        #self.gauto.Bind(wx.EVT_CHECKBOX, Closure(self.onAutoScale, color='g'))
        #self.bauto.Bind(wx.EVT_CHECKBOX, Closure(self.onAutoScale, color='b'))

        #self.rscale = FloatCtrl(self, precision=0, value=1, minval=0)
        #self.gscale = FloatCtrl(self, precision=0, value=1, minval=0)
        #self.bscale = FloatCtrl(self, precision=0, value=1, minval=0)

        ir = 0
        sizer.Add(SimpleText(self, 'Detector'),  (ir, 0), (1, 1), ALL_CEN, 2)
        sizer.Add(SimpleText(self, 'Red'),       (ir, 1), (1, 1), ALL_CEN, 2)
        sizer.Add(SimpleText(self, 'Green'),     (ir, 2), (1, 1), ALL_CEN, 2)
        sizer.Add(SimpleText(self, 'Blue'),      (ir, 3), (1, 1), ALL_CEN, 2)

        ir += 1
        sizer.Add(self.det,                  (ir, 0), (1, 1), ALL_CEN, 2)
        sizer.Add(self.rchoice,              (ir, 1), (1, 1), ALL_CEN, 2)
        sizer.Add(self.gchoice,              (ir, 2), (1, 1), ALL_CEN, 2)
        sizer.Add(self.bchoice,              (ir, 3), (1, 1), ALL_CEN, 2)

        #ir += 1
        #sizer.Add(self.rauto,            (ir, 1), (1, 1), ALL_CEN, 2)
        #sizer.Add(self.gauto,            (ir, 2), (1, 1), ALL_CEN, 2)
        #sizer.Add(self.bauto,            (ir, 3), (1, 1), ALL_CEN, 2)
        #ir += 1
        #sizer.Add(self.rscale,            (ir, 1), (1, 1), ALL_CEN, 2)
        #sizer.Add(self.gscale,            (ir, 2), (1, 1), ALL_CEN, 2)
        #sizer.Add(self.bscale,            (ir, 3), (1, 1), ALL_CEN, 2)
        #sizer.Add(SimpleText(self, 'Max Intensity:'),     (ir, 0), (1, 1), ALL_LEFT, 2)

        ir += 1
        sizer.Add(SimpleText(self, 'Normalization'), (ir, 0), (1, 1), ALL_LEFT, 2)
        sizer.Add(self.i0choice,             (ir, 1), (1, 2), ALL_LEFT, 2)

        sizer.Add(self.cor,   (ir, 3), (1, 2), ALL_LEFT, 2)

        ir += 1
        sizer.Add(self.show_new,  (ir, 0), (1, 2), ALL_LEFT, 2)
        sizer.Add(self.show_old,  (ir, 2), (1, 2), ALL_LEFT, 2)

        pack(self, sizer)

    def XXonSetRGBScale(self, event=None, color=None, **kws):
        datafile = self.owner.current_file
        det =self.det.GetStringSelection()
        if det == 'sum':
            det =  None
        else:
            det = int(det)
        dtcorrect = self.cor.IsChecked()

        if color=='r':
            roi = self.rchoice.GetStringSelection()
            map = datafile.get_roimap(roi, det=det, dtcorrect=dtcorrect)
        elif color=='g':
            roi = self.gchoice.GetStringSelection()
            map = datafile.get_roimap(roi, det=det, dtcorrect=dtcorrect)
        elif color=='b':
            roi = self.bchoice.GetStringSelection()
            map = datafile.get_roimap(roi, det=det, dtcorrect=dtcorrect)

    def onShow3ColorMap(self, event=None, new=True):
        datafile = self.owner.current_file
        det =self.det.GetStringSelection()
        if det == 'sum':
            det =  None
        else:
            det = int(det)
        dtcorrect = self.cor.IsChecked()

        r = self.rchoice.GetStringSelection()
        g = self.gchoice.GetStringSelection()
        b = self.bchoice.GetStringSelection()
        i0 = self.i0choice.GetStringSelection()
        mapshape= datafile.xrfmap['roimap/sum_cor'][:, :, 0].shape

        rmap = np.ones(mapshape, dtype='float')
        gmap = np.ones(mapshape, dtype='float')
        bmap = np.ones(mapshape, dtype='float')
        i0map = np.ones(mapshape, dtype='float')
        if r != '1':
            rmap  = datafile.get_roimap(r, det=det, dtcorrect=dtcorrect)
        if g != '1':
            gmap  = datafile.get_roimap(g, det=det, dtcorrect=dtcorrect)
        if b != '1':
            bmap  = datafile.get_roimap(b, det=det, dtcorrect=dtcorrect)
        if i0 != '1':
            i0map = datafile.get_roimap(i0, det=det, dtcorrect=dtcorrect)

        i0min = min(i0map[np.where(i0map>0)])
        i0map[np.where(i0map<=0)] = i0min
        i0map = i0map/i0map.max()

        pref, fname = os.path.split(datafile.filename)
        title = '%s: (R, G, B) = (%s, %s, %s)' % (fname, r, g, b)
        subtitles = {'red': r, 'green': g, 'blue': b}

        try:
            x = datafile.get_pos(0, mean=True)
        except:
            x = None
        try:
            y = datafile.get_pos(1, mean=True)
        except:
            y = None

        map = np.array([rmap/i0map, gmap/i0map, bmap/i0map])
        map = map.swapaxes(0, 2).swapaxes(0, 1)
        if len(self.owner.im_displays) == 0 or new:
            iframe = self.owner.add_imdisplay(title, det=det)
        self.owner.display_map(map, title=title, subtitles=subtitles,
                               x=x, y=y, det=det, xrmfile=datafile)



    def set_roi_choices(self, choices):
        roichoices = ['1']
        roichoices.extend(choices)
        for cbox in (self.rchoice, self.bchoice, self.gchoice, self.i0choice):
            set_choices(cbox, roichoices)

class AreaSelectionPanel(wx.Panel):
    delstr = """   Delete Area '%s'?

WARNING: This cannot be undone!

"""

    def __init__(self, parent, owner, **kws):
        wx.Panel.__init__(self, parent, -1, **kws)
        self.owner = owner

        sizer = wx.GridBagSizer(8, 5)

        bpanel = wx.Panel(self)
        bsizer = wx.BoxSizer(wx.HORIZONTAL)
        self.choices = {}
        self.choice = add_choice(self, choices=[], size=(150, -1), action=self.onSelect)
        self.desc   = wx.TextCtrl(self, -1,   '', size=(150, -1))
        self.info   = wx.StaticText(self, -1, '', size=(150, -1))

        self.onmap  = add_button(bpanel, 'Show on Map', size=(120, -1),
                                      action=self.onShow)
        self.clear  = add_button(bpanel, 'Clear Map', size=(120, -1),
                                      action=self.onClear)
        self.xrf    = add_button(bpanel, 'Show Spectrum', size=(120, -1),
                                      action=self.onXRF)

        self.delete = add_button(self, 'Delete Area', size=(120, -1),
                                      action=self.onDelete)
        self.update = add_button(self, 'Save Label', size=(120, -1),
                                      action=self.onLabel)

        bsizer.Add(self.onmap, 0, ALL_CEN, 2)
        bsizer.Add(self.clear, 0, ALL_CEN, 2)
        bsizer.Add(self.xrf, 0, ALL_CEN, 2)
        pack(bpanel, bsizer)
        def txt(s):
            return SimpleText(self, s)
        sizer.Add(txt('Defined Map Areas'), (0, 0), (1, 2), ALL_CEN, 2)
        sizer.Add(self.info,                (0, 2), (1, 1), ALL_RIGHT, 2)
        sizer.Add(txt('Area: '),            (1, 0), (1, 1), ALL_LEFT, 2)
        sizer.Add(self.choice,              (1, 1), (1, 1), ALL_LEFT, 2)
        sizer.Add(self.delete,              (1, 2), (1, 1), ALL_LEFT, 2)
        sizer.Add(txt('New Label: '),       (2, 0), (1, 1), ALL_LEFT, 2)
        sizer.Add(self.desc,                (2, 1), (1, 1), ALL_LEFT, 2)
        sizer.Add(self.update,              (2, 2), (1, 1), ALL_LEFT, 2)
        sizer.Add(bpanel,                   (3, 0), (1, 3), ALL_LEFT, 2)
        pack(self, sizer)

    def set_choices(self, areas, show_last=False):
        c = self.choice
        c.Clear()
        self.choices =  dict([(areas[a].attrs['description'], a) for a in areas])
        choice_labels = [areas[a].attrs['description'] for a in areas]

        c.AppendItems(choice_labels)
        if len(self.choices) > 0:
            idx = 0
            if show_last: idx = len(self.choices)-1
            this_label = choice_labels[idx]
            c.SetStringSelection(this_label)
            self.desc.SetValue(this_label)

    def _getarea(self):
        dfile = self.owner.current_file
        return self.choices[self.choice.GetStringSelection()]

    def onSelect(self, event=None):
        aname = self._getarea()
        area  = self.owner.current_file.xrfmap['areas/%s' % aname]
        npix = len(area.value[np.where(area.value)])
        self.info.SetLabel("%i Pixels" % npix)
        self.desc.SetValue(area.attrs['description'])

    def onLabel(self, event=None):
        aname = self._getarea()
        area  = self.owner.current_file.xrfmap['areas/%s' % aname]
        new_label = str(self.desc.GetValue())
        area.attrs['description'] = new_label
        self.owner.current_file.h5root.flush()
        self.set_choices(self.owner.current_file.xrfmap['areas'])
        self.choice.SetStringSelection(new_label)
        self.desc.SetValue(new_label)

    def onShow(self, event=None):
        aname = self._getarea()
        area  = self.owner.current_file.xrfmap['areas/%s' % aname]
        if len(self.owner.im_displays) > 0:
            imd = self.owner.im_displays[-1]
            imd.panel.add_highlight_area(area.value,
                                         label=area.attrs['description'])

    def onDelete(self, event=None):
        aname = self._getarea()
        erase = popup(self.owner, self.delstr % aname,
                      'Delete Area?', style=wx.YES_NO)
        if erase:
            xrfmap = self.owner.current_file.xrfmap
            del xrfmap['areas/%s' % aname]
            self.set_choices(xrfmap['areas'])

    def onClear(self, event=None):
        if len(self.owner.im_displays) > 0:
            imd = self.owner.im_displays[-1]
            for area in imd.panel.conf.highlight_areas:
                for w in area.collections + area.labelTexts:
                    w.remove()

            imd.panel.conf.highlight_areas = []
            imd.panel.redraw()

    def _getmca_area(self, areaname):
        self._mca = self.owner.current_file.get_mca_area(areaname)

    def onXRF(self, event=None):
        aname = self._getarea()
        xrmfile = self.owner.current_file
        area  = xrmfile.xrfmap['areas/%s' % aname]
        label = area.attrs['description']
        self._mca  = None

        mca_thread = Thread(target=self._getmca_area, args=(aname,))
        mca_thread.start()
        self.owner.show_XRFDisplay(xrmfile=xrmfile)
        mca_thread.join()

        pref, fname = os.path.split(self.owner.current_file.filename)
        title = "XRF Spectra:  %s, Area=%s:  %s" % (fname, aname, label)

        self.owner.xrfdisplay.SetTitle(title)
        self.owner.xrfdisplay.plotmca(self._mca)


class MapViewerFrame(wx.Frame):
    cursor_menulabels = {'lasso': ('Select Points for XRF Spectra\tCtrl+X',
                                   'Left-Drag to select points for XRF Spectra')}

    def __init__(self, conffile=None,  **kwds):

        kwds["style"] = wx.DEFAULT_FRAME_STYLE
        wx.Frame.__init__(self, None, -1, size=(700, 450),  **kwds)

        self.data = None
        self.filemap = {}
        self.im_displays = []
        self.plot_displays = []
        self.larch = None
        self.xrfdisplay = None

        self.Font12=wx.Font(12, wx.SWISS, wx.NORMAL, wx.BOLD, 0, "")
        self.Font11=wx.Font(11, wx.SWISS, wx.NORMAL, wx.BOLD, 0, "")
        self.Font10=wx.Font(10, wx.SWISS, wx.NORMAL, wx.BOLD, 0, "")
        self.Font9 =wx.Font(9, wx.SWISS, wx.NORMAL, wx.BOLD, 0, "")

        self.SetTitle("GSE XRM MapViewer")
        self.SetFont(self.Font9)

        self.createMainPanel()
        self.createMenus()
        self.statusbar = self.CreateStatusBar(2, 0)
        self.statusbar.SetStatusWidths([-3, -1])
        statusbar_fields = ["Initializing....", " "]
        for i in range(len(statusbar_fields)):
            self.statusbar.SetStatusText(statusbar_fields[i], i)

        self.htimer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.onTimer, self.htimer)
        self.h5convert_done = True
        self.h5convert_irow = 0
        self.h5convert_nrow = 0

    def CloseFile(self, filename, event=None):
        if filename in self.filemap:
            self.filemap[filename].close()
            self.filemap.pop(filename)

    def createMainPanel(self):
        sizer = wx.BoxSizer(wx.VERTICAL)
        splitter  = wx.SplitterWindow(self, style=wx.SP_LIVE_UPDATE)
        splitter.SetMinimumPaneSize(275)

        self.filelist = EditableListBox(splitter, self.ShowFile,
                                        remove_action=self.CloseFile,
                                        size=(250, -1))

        dpanel = self.detailspanel = wx.Panel(splitter)
        dpanel.SetMinSize((625, 450))
        self.createNBPanels(dpanel)
        splitter.SplitVertically(self.filelist, self.detailspanel, 1)
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(splitter, 1, wx.GROW|wx.ALL, 5)
        wx.CallAfter(self.init_larch)
        pack(self, sizer)

    def createNBPanels(self, parent):
        sizer = wx.BoxSizer(wx.VERTICAL)

        # title area:
        tpanel = wx.Panel(parent)
        self.title    = SimpleText(tpanel, 'initializing...', size=(400, -1))
        self.subtitle = SimpleText(tpanel, ' no map data   ', size=(200, -1))
        tsizer = wx.BoxSizer(wx.HORIZONTAL)
        tsizer.Add(self.title,     0, ALL_CEN)
        tsizer.Add(self.subtitle,  0, ALL_RIGHT)
        pack(tpanel, tsizer)

        sizer.Add(tpanel, 0, ALL_CEN)

        self.nb = flat_nb.FlatNotebook(parent, wx.ID_ANY, agwStyle=FNB_STYLE)
        self.nb.SetBackgroundColour('#FCFCFA')
        self.SetBackgroundColour('#F0F0E8')

        self.nbpanels = {}
        for name, key, creator in (('Simple ROI Map',  'roimap', SimpleMapPanel),
                                   ('3-Color ROI Map', '3color',  TriColorMapPanel),
                                   ('Map Math',  'mapmath',    MapMathPanel)):
            # print 'panel ' , name, parent, creator
            self.nbpanels[key] = p = creator(parent, owner=self)
            self.nb.AddPage(p, name, True)
            bgcol = p.GetBackgroundColour()

        self.nb.SetSelection(0)
        sizer.Add(self.nb, 1, wx.ALL|wx.EXPAND)

        self.area_sel = AreaSelectionPanel(parent, owner=self)
        self.area_sel.SetBackgroundColour('#F0F0E8')

        sizer.Add(wx.StaticLine(parent, size=(250, 2),
                                style=wx.LI_HORIZONTAL),
                  0,  wx.ALL|wx.EXPAND)
        sizer.Add(self.area_sel, 0, wx.ALL|wx.EXPAND)
        pack(parent, sizer)

    def get_mca_area(self, det, mask, xrmfile=None):
        if xrmfile is None:
            xrmfile = self.current_file
        aname = xrmfile.add_area(mask)
        self.sel_mca = xrmfile.get_mca_area(aname, det=det)

    def lassoHandler(self, mask=None, det=None, xrmfile=None, **kws):
        # print 'LASSO ', xrmfile,  xrmfile.filename, det
        mca_thread = Thread(target=self.get_mca_area,
                            args=(det, mask), kwargs={'xrmfile':xrmfile})
        mca_thread.start()

        self.show_XRFDisplay(xrmfile=xrmfile)
        mca_thread.join()

        if hasattr(self, 'sel_mca'):
            path, fname = os.path.split(xrmfile.filename)
            aname = self.sel_mca.areaname
            title = "XRF Spectra:  %s, Area=%s:  %s" % (fname, aname, aname)
            self.xrfdisplay.SetTitle(title)

            self.xrfdisplay.plotmca(self.sel_mca)
            self.area_sel.set_choices(self.current_file.xrfmap['areas'],
                                      show_last=True)


    def show_XRFDisplay(self, do_raise=True, clear=True, xrmfile=None):
        "make sure plot frame is enabled, and visible" 
        if xrmfile is None:
            xrmfile = self.current_file
        if self.xrfdisplay is None:
            self.xrfdisplay = XRFDisplayFrame(_larch=self.larch,
                                              gsexrmfile=xrmfile)
        try:
            self.xrfdisplay.Show()

        except wx.PyDeadObjectError:
            self.xrfdisplay = XRFDisplayFrame(_larch=self.larch,
                                              gsexrmfile=xrmfile)
            self.xrfdisplay.Show()

        if do_raise:
            self.xrfdisplay.Raise()
        if clear:
            self.xrfdisplay.panel.clear()
            self.xrfdisplay.panel.reset_config()

    def add_imdisplay(self, title, det=None):
        on_lasso = Closure(self.lassoHandler, det=det)
        imframe = MapImageFrame(output_title=title,
                                lasso_callback=on_lasso,
                                cursor_labels = self.cursor_menulabels)
        self.im_displays.append(imframe)

    def display_map(self, map, title='', info='', x=None, y=None,
                    det=None, subtitles=None, xrmfile=None):
        """display a map in an available image display"""
        displayed = False
        lasso_cb = Closure(self.lassoHandler, det=det, xrmfile=xrmfile)
        while not displayed:
            try:
                imd = self.im_displays.pop()
                imd.display(map, title=title, x=x, y=y,
                            subtitles=subtitles, det=det, xrmfile=xrmfile)
                #for col, wid in imd.wid_subtitles.items():
                #    wid.SetLabel("%s: %s" % (col.title(), subtitles[col]))
                imd.lass_callback = lasso_cb
                displayed = True
            except IndexError:
                imd = MapImageFrame(output_title=title,
                                    lasso_callback=lasso_cb,
                                    cursor_labels = self.cursor_menulabels)
                imd.display(map, title=title, x=x, y=y, subtitles=subtitles,
                            det=det, xrmfile=xrmfile)
                displayed = True
            except PyDeadObjectError:
                displayed = False
        self.im_displays.append(imd)
        imd.SetStatusText(info, 1)
        imd.Show()
        imd.Raise()

    def init_larch(self):
        self.larch = larch.Interpreter()
        #self.larch.symtable.set_symbol('_sys.wx.wxapp', wx.GetApp())
        self.larch.symtable.set_symbol('_sys.wx.parent', self)
        self.SetStatusText('ready')
        self.datagroups = self.larch.symtable
        self.title.SetLabel('')

    def ShowFile(self, evt=None, filename=None, **kws):
        if filename is None and evt is not None:
            filename = evt.GetString()

        if not self.h5convert_done or filename not in self.filemap:
            return
        if (self.check_ownership(filename) and
            self.filemap[filename].folder_has_newdata()):
            self.process_file(filename)

        self.current_file = self.filemap[filename]
        self.title.SetLabel("%s" % filename)

        ny, nx, npos = self.filemap[filename].xrfmap['positions/pos'].shape

        self.subtitle.SetLabel(" Map Size=%i x %i" % (nx, ny))

        rois = list(self.filemap[filename].xrfmap['roimap/sum_name'])
        for p in self.nbpanels.values():
            if hasattr(p, 'set_roi_choices'):
                p.set_roi_choices(rois)
            if hasattr(p, 'set_file_choices'):
                p.set_file_choices(self.filemap.keys())

        self.area_sel.set_choices(self.current_file.xrfmap['areas'])

    def createMenus(self):
        self.menubar = wx.MenuBar()
        fmenu = wx.Menu()
        add_menu(self, fmenu, "&Open Map File\tCtrl+O",
                 "Read Map File",  self.onReadFile)
        add_menu(self, fmenu, "&Open Map Folder\tCtrl+F",
                 "Read Map Folder",  self.onReadFolder)
        add_menu(self, fmenu, 'Change &Working Folder',
                  "Choose working directory",
                  self.onFolderSelect)
        fmenu.AppendSeparator()
        add_menu(self, fmenu, "&Quit\tCtrl+Q",
                  "Quit program", self.onClose)

        hmenu = wx.Menu()
        add_menu(self, hmenu, 'About', 'About MapViewer', self.onAbout)

        self.menubar.Append(fmenu, "&File")
        self.menubar.Append(hmenu, "&Help")
        self.SetMenuBar(self.menubar)

    def onFolderSelect(self,evt):
        style = wx.DD_DIR_MUST_EXIST|wx.DD_DEFAULT_STYLE
        dlg = wx.DirDialog(self, "Select Working Directory:", os.getcwd(),
                           style=style)

        if dlg.ShowModal() == wx.ID_OK:
            basedir = os.path.abspath(str(dlg.GetPath()))
            try:
                os.chdir(nativepath(basedir))
            except OSError:
                print 'Changed folder failed'
                pass
        dlg.Destroy()

    def onAbout(self, evt):
        about = """GSECARS X-ray Microprobe Map Viewer:
Matt Newville <newville @ cars.uchicago.edu>
    MapViewer version: %s
    Built with X-ray Larch version: %s
    """  % (__version__, larch.__version__)

        dlg = wx.MessageDialog(self, about, "About GSE XRM MapViewer",
                               wx.OK | wx.ICON_INFORMATION)
        dlg.ShowModal()
        dlg.Destroy()

    def onClose(self, evt):

        for xrmfile in self.filemap.values():
            xrmfile.close()

        for disp in self.im_displays + self.plot_displays:
            try:
                disp.Destroy()
            except:
                pass

        try:
            self.xrfdisplay.Destroy()
        except:
            pass

        for nam in dir(self.larch.symtable._plotter):
            obj = getattr(self.larch.symtable._plotter, nam)
            try:
                obj.Destroy()
            except:
                pass
        for nam in dir(self.larch.symtable._sys.wx):
            obj = getattr(self.larch.symtable._sys.wx, nam)
            del obj
        self.Destroy()

    def onReadFolder(self, evt=None):
        if not self.h5convert_done:
            print 'cannot open file while processing a map folder'
            return

        dlg = wx.DirDialog(self, message="Read Map Folder",
                           defaultPath=os.getcwd(),
                           style=wx.OPEN)

        path, read = None, False
        if dlg.ShowModal() == wx.ID_OK:
            read = True
            path = dlg.GetPath().replace('\\', '/')
        dlg.Destroy()
        if read:
            try:
                xrmfile = GSEXRM_MapFile(folder=str(path))
            except:
                popup(self, NOT_GSEXRM_FOLDER % str(path),
                     "Not a Map folder")
                return
            fname = xrmfile.filename
            if fname not in self.filemap:
                self.filemap[fname] = xrmfile
            if fname not in self.filelist.GetItems():
                self.filelist.Append(fname)
            if self.check_ownership(fname):
                self.process_file(fname)
            self.ShowFile(filename=fname)

    def onReadFile(self, evt=None):
        if not self.h5convert_done:
            print 'cannot open file while processing a map folder'
            return

        dlg = wx.FileDialog(self, message="Read Map File",
                            defaultDir=os.getcwd(),
                            wildcard=FILE_WILDCARDS,
                            style=wx.OPEN)
        path, read = None, False
        if dlg.ShowModal() == wx.ID_OK:
            read = True
            path = dlg.GetPath().replace('\\', '/')
            if path in self.filemap:
                read = popup(self, "Re-read file '%s'?" % path, 'Re-read file?',
                             style=wx.YES_NO)

        dlg.Destroy()

        if read:
            parent, fname = os.path.split(path)
            xrmfile = GSEXRM_MapFile(filename=str(path))

            #try:
            #except:
            #    popup(self, NOT_GSEXRM_FILE % fname,
            #          "Not a Map file!")
            #    return
            if fname not in self.filemap:
                self.filemap[fname] = xrmfile
            if fname not in self.filelist.GetItems():
                self.filelist.Append(fname)
            if self.check_ownership(fname):
                self.process_file(fname)
            self.ShowFile(filename=fname)

    def onGSEXRM_Data(self,  **kws):
        print 'Saw GSEXRM_Data ', kws

    def process_file(self, filename):
        """Request processing of map file.
        This can take awhile, so is done in a separate thread,
        with updates displayed in message bar
        """
        xrm_map = self.filemap[filename]
        if xrm_map.status == GSEXRM_FileStatus.created:
            xrm_map.initialize_xrfmap()

        if xrm_map.dimension is None and isGSEXRM_MapFolder(self.folder):
            xrm_map.read_master()

        if self.filemap[filename].folder_has_newdata():
            self.h5convert_fname = filename
            self.h5convert_done = False
            self.h5convert_irow, self.h5convert_nrow = 0, 0
            self.h5convert_t0 = time.time()
            self.htimer.Start(150)
            self.h5convert_thread = Thread(target=self.new_mapdata,
                                           args=(filename,))
            self.h5convert_thread.start()
            # self.new_mapdata(filename)

    def onTimer(self, event):
        fname, irow, nrow = self.h5convert_fname, self.h5convert_irow, self.h5convert_nrow
        self.message('MapViewer Timer Processing %s:  row %i of %i' % (fname, irow, nrow))
        if self.h5convert_done:
            self.htimer.Stop()
            self.h5convert_thread.join()
            self.message('MapViewerTimer Processing %s: complete!' % fname)
            self.ShowFile(filename=self.h5convert_fname)

    def new_mapdata(self, filename):
        xrm_map = self.filemap[filename]
        nrows = len(xrm_map.rowdata)
        self.h5convert_nrow = nrows
        self.h5convert_done = False
        if xrm_map.folder_has_newdata():
            irow = xrm_map.last_row + 1
            self.h5convert_irow = irow
            while irow < nrows:
                t0 = time.time()
                self.h5convert_irow = irow
                rowdat = xrm_map.read_rowdata(irow)
                t1 = time.time()
                xrm_map.add_rowdata(rowdat)
                t2 = time.time()
                irow  = irow + 1
                try:
                    wx.Yield()
                except:
                    pass

        xrm_map.resize_arrays(xrm_map.last_row+1)
        xrm_map.h5root.flush()
        self.h5convert_done = True
        time.sleep(0.025)

    def message(self, msg, win=0):
        self.statusbar.SetStatusText(msg, win)

    def check_ownership(self, fname):
        """
        check whether we're currently owner of the file.
        this is important!! HDF5 files can be corrupted.
        """
        if not self.filemap[fname].check_hostid():
            if popup(self, NOT_OWNER_MSG % fname,
                     'Not Owner of HDF5 File', style=wx.YES_NO):
                self.filemap[fname].claim_hostid()
        return self.filemap[fname].check_hostid()

class MapViewer(wx.App):
    def __init__(self, **kws):
        wx.App.__init__(self, **kws)

    def run(self):
        self.MainLoop()

    def createApp(self):
        frame = MapViewerFrame()
        frame.Show()
        self.SetTopWindow(frame)

    def OnInit(self):
        self.createApp()
        return True

class DebugViewer(MapViewer, wx.lib.mixins.inspection.InspectionMixin):
    def __init__(self, **kws):
        MapViewer.__init__(self, **kws)

    def OnInit(self):
        self.Init()
        self.createApp()
        self.ShowInspectionTool()
        return True

if __name__ == "__main__":
    DebugViewer().run()