import io
import numpy as np
from PIL import Image
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors

# Configure once at module import
_CMAP = plt.get_cmap('RdYlGn')
_NORM = mcolors.Normalize(vmin=-0.2, vmax=1.0)


def create_ndvi_buffer(ndvi: np.ndarray) -> io.BytesIO:
    buf = io.BytesIO()
    fig, ax = plt.subplots(figsize=(8, 8))
    im = ax.imshow(ndvi, cmap=_CMAP, norm=_NORM)
    ax.axis('off')
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04, label='NDVI')
    plt.savefig(buf, format='png', bbox_inches='tight', dpi=150)
    plt.close(fig)
    buf.seek(0)
    return buf


def normalize_image(job_id: str, img: np.ndarray, lower: float = 2, upper: float = 98) -> np.ndarray:
    valid = img[~np.isnan(img)]
    if valid.size == 0:
        return np.zeros_like(img)
    vmin, vmax = np.percentile(valid, [lower, upper])
    if vmax - vmin < 1e-6:
        return np.zeros_like(img)
    norm = (img - vmin) / (vmax - vmin)
    return np.clip(norm, 0, 1)


def create_rgb_buffer(rgb: np.ndarray) -> io.BytesIO:
    buf = io.BytesIO()
    rgb_norm = normalize_image('', rgb)
    rgb_uint8 = (np.clip(rgb_norm, 0, 1) * 255).astype(np.uint8)
    if rgb_uint8.ndim == 3 and rgb_uint8.shape[0] == 3:
        rgb_uint8 = np.transpose(rgb_uint8, (1, 2, 0))
    img = Image.fromarray(rgb_uint8, 'RGB')
    img.save(buf, format='PNG')
    buf.seek(0)
    return buf