public class Main {
    public static void main(String[] args) {
        Solution solution = new Solution();
        int []arr={1,2,3,4};

        int numEqualsOne = solution.findNumEqualsOne(arr);
        solution.printArray(arr);
    }
}

class Solution{
    public int findNumEqualsOne(int[] nums) {
        int max = 0;
        int count = 0;
        for (int i = 0; i < nums.length; i++) {
            if (nums[i] == 1) {
                count++;
            }
        }
        return 1;
    }
    public void printArray(int[] nums){
        for (int i = 0; i < nums.length; i++) {
            System.out.print(nums[i] + " ");
        }
    }
}