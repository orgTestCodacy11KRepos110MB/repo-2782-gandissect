import torch, argparse, sys, os, numpy
from netdissect.sampler import FixedRandomSubsetSampler, FixedSubsetSampler
from torch.utils.data import DataLoader
from torchvision import transforms
from netdissect.progress import default_progress, verbose_progress
from netdissect import zdataset
from netdissect import segmenter
from netdissect import frechet_distance
from netdissect import parallelfolder

NUM_OBJECTS=336

def main():
    parser = argparse.ArgumentParser(description='Net dissect utility',
            prog='python -m netdissect.fsd')
    parser.add_argument('true_dir')
    parser.add_argument('gen_dir')
    parser.add_argument('--size', type=int, default=10000)
    parser.add_argument('--cachedir', default=None)
    parser.add_argument('--histout', default=None)
    parser.add_argument('--maxscale', type=float, default=50.0)
    parser.add_argument('--labelcount', type=int, default=30)
    parser.add_argument('--dpi', type=float, default=100)
    if len(sys.argv) == 1:
        parser.print_usage(sys.stderr)
        sys.exit(1)
    args = parser.parse_args()
    verbose_progress(True)
    true_dir, gen_dir = args.true_dir, args.gen_dir
    true_tally, gen_tally = [
            cached_tally_directory(d, size=args.size, cachedir=args.cachedir)
            for d in [true_dir, gen_dir]]
    fsd, meandiff, covdiff = frechet_distance.sample_frechet_distance(
            true_tally * 100, gen_tally * 100, return_components=True)
    print('fsd: %f; meandiff: %f; covdiff: %f' % (fsd, meandiff, covdiff))
    if args.histout is not None:
        diff_figure(true_tally, gen_tally,
                labelcount=args.labelcount,
                maxscale=args.maxscale,
                dpi=args.dpi
                ).savefig(args.histout)


def cached_tally_directory(directory, size=10000, cachedir=None):
    filename = '%s_segtally_%d.npy' % (directory, size)
    if cachedir is not None:
        filename = os.path.join(cachedir,
                os.path.abspath(filename).replace('/', '_'))
    if os.path.isfile(filename):
        return numpy.load(filename)
    os.makedirs(cachedir, exist_ok=True)
    result = tally_directory(directory, size)
    numpy.save(filename, result)
    return result

def tally_directory(directory, size=10000):
    progress = default_progress()
    dataset = parallelfolder.ParallelImageFolders(
                [directory],
                transform=transforms.Compose([
                    transforms.Resize(256),
                    transforms.CenterCrop(256),
                    transforms.ToTensor(),
                    transforms.Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5))
                    ]))
    loader = DataLoader(dataset,
                        sampler=FixedRandomSubsetSampler(dataset, end=size),
                        # sampler=FixedSubsetSampler(range(size)),
                        batch_size=10, pin_memory=True)
    upp = segmenter.UnifiedParsingSegmenter()
    labelnames, catnames = upp.get_label_and_category_names()
    result = numpy.zeros((size, NUM_OBJECTS), dtype=numpy.float)
    batch_result = torch.zeros(loader.batch_size, NUM_OBJECTS,
            dtype=torch.float).cuda()
    with torch.no_grad():
        batch_index = 0
        for [batch] in progress(loader):
            seg_result = upp.segment_batch(batch.cuda())
            for i in range(len(batch)):
                batch_result[i] = (
                    seg_result[i,0].view(-1).bincount(
                        minlength=NUM_OBJECTS).float()
                    / (seg_result.shape[2] * seg_result.shape[3])
                )
            result[batch_index:batch_index+len(batch)] = (
                    batch_result.cpu().numpy())
            batch_index += len(batch)
    return result

def tally_dataset_objects(dataset, size=10000):
    progress = default_progress()
    loader = DataLoader(dataset,
                        sampler=FixedRandomSubsetSampler(dataset, end=size),
                        batch_size=10, pin_memory=True)
    upp = segmenter.UnifiedParsingSegmenter()
    labelnames, catnames = upp.get_label_and_category_names()
    result = numpy.zeros((size, NUM_OBJECTS), dtype=numpy.float)
    batch_result = torch.zeros(loader.batch_size, NUM_OBJECTS,
            dtype=torch.float).cuda()
    with torch.no_grad():
        batch_index = 0
        for [batch] in progress(loader):
            seg_result = upp.segment_batch(batch.cuda())
            for i in range(len(batch)):
                batch_result[i] = (
                    seg_result[i,0].view(-1).bincount(
                        minlength=NUM_OBJECTS).float()
                    / (seg_result.shape[2] * seg_result.shape[3])
                )
            result[batch_index:batch_index+len(batch)] = (
                    batch_result.cpu().numpy())
            batch_index += len(batch)
    return result

def tally_generated_objects(model, size=10000):
    progress = default_progress()
    zds = zdataset.z_dataset_for_model(model, size)
    loader = DataLoader(zds, batch_size=10, pin_memory=True)
    upp = segmenter.UnifiedParsingSegmenter()
    labelnames, catnames = upp.get_label_and_category_names()
    result = numpy.zeros((size, NUM_OBJECTS), dtype=numpy.float)
    batch_result = torch.zeros(loader.batch_size, NUM_OBJECTS,
            dtype=torch.float).cuda()
    with torch.no_grad():
        batch_index = 0
        for [zbatch] in progress(loader):
            img = model(zbatch.cuda())
            seg_result = upp.segment_batch(img)
            for i in range(len(zbatch)):
                batch_result[i] = (
                    seg_result[i,0].view(-1).bincount(
                        minlength=NUM_OBJECTS).float()
                    / (seg_result.shape[2] * seg_result.shape[3])
                    )
            result[batch_index:batch_index+len(zbatch)] = (
                    batch_result.cpu().numpy())
            batch_index += len(zbatch)
    return result

def diff_figure(ttally, gtally,
              labelcount=30, labelleft=True, dpi=100,
              maxscale=50.0, legend=False):
    from matplotlib.backends.backend_agg import FigureCanvasAgg as FigureCanvas
    from matplotlib.figure import Figure
    tresult, gresult = [t.mean(0) for t in [ttally, gtally]]
    upp = segmenter.UnifiedParsingSegmenter()
    labelnames, catnames = upp.get_label_and_category_names()
    x = []
    labels = []
    gen_amount = []
    change_frac = []
    true_amount = []
    for label in numpy.argsort(-tresult):
        if label == 0 or labelnames[label][1] == 'material':
            continue
        if tresult[label] == 0:
            break
        x.append(len(x))
        labels.append(labelnames[label][0].split()[0])
        true_amount.append(tresult[label].item())
        gen_amount.append(gresult[label].item())
        change_frac.append((float(gresult[label] - tresult[label])
                            / tresult[label]))
        if len(x) >= labelcount:
            break
    fig = Figure(dpi=dpi, figsize=(1.4 + 5.0 * labelcount / 30, 4.8))
    FigureCanvas(fig)
    a1, a0 = fig.subplots(2, 1, gridspec_kw = {'height_ratios':[1, 2]})
    a0.bar(x, change_frac, label='relative delta')
    a0.set_xticks(x)
    a0.set_xticklabels(labels, rotation='vertical')
    if labelleft:
        a0.set_ylabel('relative delta\n(gen - train) / train')
    a0.set_xlim(-1.0, len(x))
    a0.set_ylim([-1, 1.1])
    a0.grid(axis='y', antialiased=False, alpha=0.25)
    if legend:
        a0.legend(loc=2)
    prev_high = None
    for ix, cf in enumerate(change_frac):
        if cf > 1.15:
            if prev_high == (ix - 1):
                offset = 0.1
            else:
                offset = 0.0
                prev_high = ix
            a0.text(ix, 1.15 + offset,
                    '%.1f' % cf, horizontalalignment='center', size=6)

    a1.bar(x, true_amount, label='training')
    a1.plot(x, gen_amount, linewidth=3, color='red', label='generated')
    a1.set_yscale('log')
    a1.set_xlim(-1.0, len(x))
    a1.set_ylim(maxscale / 5000, maxscale)
    if labelleft:
        a1.set_ylabel('mean area\nlog scale')
    if legend:
        a1.legend()
    a1.set_xticks([])
    fig.tight_layout()
    return fig

if __name__ == '__main__':
    main()