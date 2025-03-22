import os
import onnxruntime as ort
import numpy as np
import cv2
from rembg import remove
from PIL import Image
from skimage.metrics import structural_similarity as ssim

# Load ONNX model
model_path = "u2net_human_seg.onnx"
session = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])


# Preprocess image
def preprocess(image_path):
    image = Image.open(image_path).convert("RGB")
    orig_size = image.size  # Save original size for later
    image = image.resize((320, 320))  # Resize for U-2-Net
    image = np.array(image).astype(np.float32) / 255.0  # Normalize
    image = np.transpose(image, (2, 0, 1))  # Convert to (C, H, W)
    image = np.expand_dims(image, axis=0)  # Add batch dim
    return image, orig_size


# Postprocess mask
def postprocess(output, orig_size):
    mask = output.squeeze()  # Remove batch dimension
    mask = cv2.resize(mask, orig_size)  # Resize back to original size
    mask = (mask > 0.5).astype(np.uint8) * 255  # Convert to binary mask
    return mask


# Remove background using U-2-Net
def remove_background_u2net(image_path):
    image = cv2.imread(image_path, cv2.IMREAD_UNCHANGED)
    orig_h, orig_w = image.shape[:2]

    preprocessed_img, orig_size = preprocess(image_path)
    input_name = session.get_inputs()[0].name
    output_name = session.get_outputs()[0].name
    output = session.run([output_name], {input_name: preprocessed_img})[0]
    mask = postprocess(output, orig_size)

    mask = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
    result = cv2.bitwise_and(image, mask)
    result_rgba = cv2.cvtColor(result, cv2.COLOR_BGR2BGRA)
    result_rgba[:, :, 3] = mask[:, :, 0]  # Alpha channel from mask

    # Convert BGR to RGB
    result_rgb = cv2.cvtColor(result_rgba, cv2.COLOR_BGRA2RGBA)

    return result_rgb


# Remove background using rembg
def remove_background_rembg(image_path):
    input_image = Image.open(image_path)
    output_image = remove(input_image)
    return np.array(output_image)


# Compare results using SSIM
def compare_images(img1, img2):
    gray1 = cv2.cvtColor(img1, cv2.COLOR_BGRA2GRAY)
    gray2 = cv2.cvtColor(img2, cv2.COLOR_BGRA2GRAY)
    return ssim(gray1, gray2)


# Foreground pixel analysis
def foreground_pixel_analysis(output_img):
    alpha_channel = output_img[:, :, 3]
    return np.count_nonzero(alpha_channel) / alpha_channel.size


# Edge detection similarity
def edge_similarity(img1, img2):
    edges1 = cv2.Canny(cv2.cvtColor(img1, cv2.COLOR_BGRA2GRAY), 100, 200)
    edges2 = cv2.Canny(cv2.cvtColor(img2, cv2.COLOR_BGRA2GRAY), 100, 200)
    return ssim(edges1, edges2)


# Histogram difference
def histogram_difference(img1, img2):
    hist1 = cv2.calcHist([img1], [0], None, [256], [0, 256])
    hist2 = cv2.calcHist([img2], [0], None, [256], [0, 256])
    return cv2.compareHist(hist1, hist2, cv2.HISTCMP_CORREL)


# Decide the best method for background removal
def decide_best_method(output_u2net, output_rembg, original_image,image_path, min_foreground_threshold=0.2):
    score_ssim = compare_images(output_u2net, output_rembg)
    score_foreground = foreground_pixel_analysis(output_u2net)  # Measure how much foreground remains
    score_edge = edge_similarity(output_u2net, output_rembg)
    score_hist = histogram_difference(output_u2net, output_rembg)

    # If the foreground proportion is too low, return the original image
    if needs_background_removal(image_path) and score_foreground < min_foreground_threshold:
        print("Skipping background removal: Foreground too small")
        return original_image, "Original"

    # Compute the weighted scores
    u2net_score = (score_ssim + score_foreground + score_edge + score_hist) / 4
    rembg_score = (1 - score_ssim + (1 - score_foreground) + (1 - score_edge) + (1 - score_hist)) / 4

    best_output = output_u2net if u2net_score > rembg_score else output_rembg
    best_method = "U-2-Net" if u2net_score > rembg_score else "rembg"

    return best_output, best_method


def process_image(image_path):
    original_image = cv2.imread(image_path, cv2.IMREAD_UNCHANGED)  # Load the original image
    original_image = cv2.cvtColor(original_image, cv2.COLOR_BGR2RGB)  # Convert BGR to RGB

    # Remove background using U-2-Net and rembg
    output_u2net = remove_background_u2net(image_path)
    output_rembg = remove_background_rembg(image_path)

    # Decide which method provides the best result
    best_output, best_method = decide_best_method(output_u2net, output_rembg, original_image, image_path)

    # Replace transparent background with white in the selected output
    best_output_with_white_bg = replace_with_white_background(best_output)

    print(f"Using method: {best_method}")
    return best_output_with_white_bg


# Replace transparent or background pixels with a white background
def replace_with_white_background(image):
    if image.shape[2] == 4:  # If the image has an alpha channel (RGBA)
        # Extract the alpha channel
        alpha_channel = image[:, :, 3]

        # Create a mask for the transparent pixels
        transparent_mask = alpha_channel == 0

        # Replace transparent pixels with white (255, 255, 255)
        image[transparent_mask] = [255, 255, 255, 255]  # Set the RGB channels to white and keep alpha intact

    else:
        # If the image is RGB, just return it as is (no transparency to replace)
        pass

    return image


# **Improved: Decide whether to remove background at all**
def needs_background_removal(img_path):
    image = cv2.imread(img_path, cv2.IMREAD_COLOR)
    saliency = cv2.saliency.StaticSaliencyFineGrained_create()
    _, saliency_map = saliency.computeSaliency(image)

    threshold = 0.4  # Adjusted based on experiments
    saliency_score = np.mean(saliency_map)

    # Alternative condition: Only remove background if a clear subject exists
    #high_saliency_ratio = np.sum(saliency_map > 0.6) / saliency_map.size

    return saliency_score < threshold

# Process and save the best result
def process_and_save(image_path, output_path):
    processed_image = process_image(image_path)
    Image.fromarray(processed_image).save(output_path, "PNG")


# Process all images in a directory
def process_directory(input_dir, output_dir):
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    image_extensions = (".jpg", ".jpeg", ".png")
    for filename in os.listdir(input_dir):
        if filename.lower().endswith(image_extensions):
            input_path = os.path.join(input_dir, filename)
            output_path = os.path.join(output_dir, os.path.splitext(filename)[0] + ".png")
            process_and_save(input_path, output_path)


# Example Usage
if __name__ == "__main__":
    input_folder = "Ak1n02 bg-rem test_eren First_Degree"
    output_folder = "Ak1n02 bg-rem test_eren First_Degree_removed"
    process_directory(input_folder, output_folder)
