import cv2
from matplotlib import pyplot as plt
import numpy as np
from random import randint, sample


def preProcess(img):
    # Convert to grayscale
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)

    # Blur image to remove noise
    # Determine kernel size by using image height and width
    height, width, _ = img.shape
    kernel_size = min(int(height * 0.0025), int(width * 0.0025))
    # Kernel must have odd values because of GaussianBlur
    if kernel_size % 2 == 0:
        kernel_size += 1
    kernel = (kernel_size, kernel_size)
    print("kernel: " + str(kernel))
    blur = cv2.GaussianBlur(gray, kernel, 1)

    # Use adaptive thresholding to have only black and white pixels
    # Without adaptive shadows might black out regions in the image
    # Gaussian produces less noise compared to ADAPTIVE_THRESH_MEAN_C
    # Block size: above, both kernel values are odd, but block size must be even, therefore add 1.
    block_size = kernel_size * 2 + 1
    threshold = cv2.adaptiveThreshold(blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, block_size, 2)

    # Use laplacian to detect gradients in the image (i.e. lines)
    # This helps to improve table region detection in later stages
    laplacian = cv2.Laplacian(threshold, cv2.CV_64F)
    # Convert data type from 64f to unsigned 8-bit integer
    laplacian = np.uint8(np.absolute(laplacian))

    return (threshold, laplacian)


def findLargestQuadrilateralContour(contours, maxArea=None):
    maxAreaSet = maxArea is not None
    biggest_area = 0
    biggest_contour = None
    biggest_contour_approx = None
    for contour in contours:
        # Get the area of this contour
        area = cv2.contourArea(contour)

        # Reassign maxArea if it was originally None
        if not maxAreaSet:
            maxArea = area

        # Get the length of the perimeter
        perimeter = cv2.arcLength(contour, True)

        # Approximate a shape that resembles the contour
        # This is needed because the image might be warped, thus
        # edges are curved and not perfectly straight
        approx = cv2.approxPolyDP(contour, 0.01 * perimeter, True)

        # Check if area is bigger than previous contour but smaller than or equal to maxArea
        # and if the approximation contains only 4 sides (i.e. quadrilateral)
        if biggest_area < area <= maxArea and len(approx) == 4:
            biggest_area = area
            biggest_contour = contour
            biggest_contour_approx = approx
    return [biggest_contour], [biggest_contour_approx]


def processContour(approx):
    # Reshape array([x, y], ...) to array( array([x], [y]), ...)
    approx = approx.reshape((4, 2))

    # Sort points in clockwise order, starting from top left
    pts = np.zeros((4, 2), dtype=np.float32)

    # Add up all values
    # Smallest sum = top left point
    # Largest sum = bottom right point
    s = approx.sum(axis=1)
    pts[0] = approx[np.argmin(s)]
    pts[2] = approx[np.argmax(s)]

    # For the other 2 points, compute difference between all points
    # Smallest difference = top right point
    # Largest difference = bottom left point
    diff = np.diff(approx, axis=1)
    pts[1] = approx[np.argmin(diff)]
    pts[3] = approx[np.argmax(diff)]

    # Calculate smallest height and width
    width = int(min(pts[1][0] - pts[0][0], pts[2][0] - pts[3][0]))
    height = int(min(pts[3][1] - pts[0][1], pts[2][1] - pts[1][1]))

    return pts, width, height


def findLinesAndIntersections(img):
    # Adapted from https://docs.opencv.org/4.4.0/dd/dd7/tutorial_morph_lines_detection.html

    # Get image height and width to dynamically change
    # horizontal and vertical kernel sizes
    height, width = img.shape
    
    # Increase thickness of lines in the image
    erosion = cv2.erode(img, np.ones((3, 3)))

    # To find horizontal lines, run a horizontal kernel (e.g. [1 1 1 1])
    # Dilation finds lines, but shrinks their lengths, so
    # follow with Erosion to restore original lines' size
    horizontal_kernel = np.ones((1, width // 30))
    horizontal = cv2.dilate(erosion, horizontal_kernel)
    horizontal = cv2.erode(horizontal, horizontal_kernel)
    
    # To find vertical lines, run a vertical kernel (e.g. [1
    vertical_kernel = np.ones((height // 30, 1))         # 1
    vertical = cv2.dilate(erosion, vertical_kernel)      # 1
    vertical = cv2.erode(vertical, vertical_kernel)      # 1])

    lines = cv2.bitwise_and(vertical, horizontal)
    lines = cv2.erode(lines, np.ones((3, 3)), iterations=3)

    # Binarize the image, values less than 250 (almsot full white) are
    # treated as black pixels, improving cell contour detection.
    # Without this, grey lines are sometimes ignored by cv2.findContours
    _, lines = cv2.threshold(lines, 250, 255, cv2.THRESH_BINARY)

    return lines


def extractRows(cell_contours):
    # Get a subset of the cell contours (10%) and compute an average cell height
    sample_cells = sample(cell_contours, int(len(cell_contours) * 0.1))
    avg_height = sum([cv2.boundingRect(cnt)[3] for cnt in sample_cells]) // len(sample_cells)
    print("Average cell height: " + str(avg_height))

    rows = {}

    for cnt in cell_contours:
        # Approximate contour to a rectangle, get x, y, width and height
        _, y, _, height = cv2.boundingRect(cnt)
        # x, y are coordinates of the top-left point, get the center of rectangle
        y = y + int(height / 2)

        # Keep track of whether the contour has been assigned to a row
        added = False

        # Iterate over existing rows where:
        # row = y-coordinate of the row
        for row in rows.keys():
            # Add this contour to the row that is within a margin of error
            # (± avg cell height)
            # This simple algorithm works well because of the table warping,
            # meanining all rows should be horizontally parallel to each other.
            if (row - avg_height) <= y <= (row + avg_height):
                rows[row].append(cnt)
                added = True
                break

        # If the row wasn't added, create a new row with this cell's y-coordinate
        # as the row
        if not added:
            rows[y] = [cnt]

    # Sort rows top to bottom.
    rows = dict(sorted(rows.items()))

    # Sort cells left to right
    for key, value in rows.items():
        rows[key] = sorted(value, key=lambda cnt: cv2.boundingRect(cnt)[0])
    
    return rows


def main():
    # READ IMAGE
    img = cv2.imread("data/sample_table.jpg")
    img_copy = img.copy()
    height, width, _ = img.shape

    # PROCESS IMAGE
    threshold, laplacian = preProcess(img_copy)

    # FIND CONTOUR
    contours, _ = cv2.findContours(laplacian, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)

    # FIND TABLE REGION
    # It is assumed the table takes up most of the image (less than 95%),
    # thus it can be identified by finding the largest contour with 4 sides
    maxArea = width * height * 0.95
    table_contour, table_contour_approx = findLargestQuadrilateralContour(contours, maxArea)
    table_pts, table_width, table_height = processContour(table_contour_approx[0])

    # EXTRACT TABLE REGION
    # Start with a full black image
    table_img = np.zeros(threshold.shape).astype(threshold.dtype)
    # Create a mask for the table region
    cv2.fillPoly(table_img, table_contour, (255, 255, 255))
    # Apply the mask to the thresholded image, filling the region
    # outside of the table with white
    table_img = cv2.bitwise_and(threshold, table_img)

    # WARP TABLE
    # Use warp to extract the table region from the processed image
    # by mapping table points to a new image of size table_width x table_height
    target_points = np.float32([[0, 0], [table_width, 0], [table_width, table_height], [0, table_height]])
    matrix = cv2.getPerspectiveTransform(table_pts, target_points)
    # Apply warp to threshold image
    warped = cv2.warpPerspective(table_img, matrix, (table_width, table_height))

    # FIND HORIZONTAL & VERTICAL LINES
    # Find horizontal and vertical lines
    lines = findLinesAndIntersections(warped)

    # EXTRACT CELLS
    # Get each cell's contour
    cell_contours, _ = cv2.findContours(lines, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    print("Found " + str(len(cell_contours)) + " cells")
    # Group cells by row
    rows = extractRows(cell_contours)

    # FOR DEBUG PURPOSES ONLY
    images = [(img, "original"), (threshold, "threshold"), (laplacian, "laplacian")]

    # Create new image with table contour displayed on top of processed image
    table_contour_image = cv2.cvtColor(laplacian.copy(), cv2.COLOR_GRAY2BGR)
    cv2.drawContours(table_contour_image, table_contour, -1, (0, 0, 255), 10)  # Contour
    cv2.drawContours(table_contour_image, table_contour_approx, -1, (0, 255, 0), 10)  # Approximation
    images.append((table_contour_image, "contour"))
    images.append((table_img, "table"))
    images.append((warped, "warped"))
    images.append((lines, "lines"))
    # Create new image to display cell contours
    cell_contours_image = lines.copy()
    cell_contours_image = cv2.cvtColor(cell_contours_image, cv2.COLOR_GRAY2BGR)
    cv2.drawContours(cell_contours_image, cell_contours, -1, (0, 0, 255), 15)
    # Add overlay showing contour index in image
    for i, cnt in enumerate(cell_contours):
        # Get contour coordinates
        # Refer to https://opencv-python-tutroals.readthedocs.io/en/latest/py_tutorials/py_imgproc/py_contours/py_contour_features/py_contour_features.html#moments
        M = cv2.moments(cnt)
        coordinates = (int(M['m10']/(M['m00'] + 1)), int(M['m01']/(M['m00'] + 1)))
        # Put text with index contour index at above coordinates
        cv2.putText(cell_contours_image, str(i), coordinates, cv2.FONT_HERSHEY_DUPLEX, 1.5, (255, 0, 0))
    images.append((cell_contours_image, "cell contours"))
    # Create new image to display detected rows
    rows_img = warped.copy()
    rows_img = cv2.cvtColor(rows_img, cv2.COLOR_GRAY2BGR)
    # Colour-coordinate cells based on row and display cell contour index
    for _, value in rows.items():
        color = (randint(0, 255), randint(0, 255), randint(0, 255))
        cv2.drawContours(rows_img, value, -1, color, 15)
        for i, cnt in enumerate(value):
            M = cv2.moments(cnt)
            coordinates = (int(M['m10']/(M['m00'] + 1)), int(M['m01']/(M['m00'] + 1)))
            cv2.putText(rows_img, str(i), coordinates, cv2.FONT_HERSHEY_DUPLEX, 3, (0, 0, 255), 2)
    images.append((rows_img, "rows"))

    # Show images
    for image, title in images:
        plt.figure(title)
        plt.imshow(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
    plt.show()


if __name__ == '__main__':
    main()
